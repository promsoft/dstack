import subprocess
import tempfile
import time
from typing import List, Optional

from kubernetes import client

from dstack._internal.core.backends.base.compute import (
    Compute,
    get_docker_commands,
    get_instance_name,
)
from dstack._internal.core.backends.kubernetes.client import get_api
from dstack._internal.core.backends.kubernetes.config import KubernetesConfig
from dstack._internal.core.models.backends.base import BackendType
from dstack._internal.core.models.instances import (
    InstanceAvailability,
    InstanceOfferWithAvailability,
    InstanceType,
    LaunchedGatewayInfo,
    LaunchedInstanceInfo,
    Resources,
    SSHConnectionParams,
)
from dstack._internal.core.models.runs import Job, Requirements, Run
from dstack._internal.utils.logging import get_logger

logger = get_logger(__name__)


class KubernetesCompute(Compute):
    def __init__(self, config: KubernetesConfig):
        self.config = config
        self.api = get_api(config.kubeconfig)

    def get_offers(
        self, requirements: Optional[Requirements] = None
    ) -> List[InstanceOfferWithAvailability]:
        # TODO: Use self.client.list_node()?
        return [
            InstanceOfferWithAvailability(
                backend=BackendType.KUBERNETES,
                instance=InstanceType(
                    name="k8s-instance",
                    resources=Resources(
                        cpus=2,
                        memory_mib=8192,
                        gpus=[],
                        spot=False,
                    ),
                ),
                price=0,
                region="local",
                availability=InstanceAvailability.AVAILABLE,
            )
        ]

    def run_job(
        self,
        run: Run,
        job: Job,
        instance_offer: InstanceOfferWithAvailability,
        project_ssh_public_key: str,
        project_ssh_private_key: str,
    ) -> LaunchedInstanceInfo:
        instance_name = get_instance_name(run, job)
        commands = get_docker_commands(
            [run.run_spec.ssh_key_pub.strip(), project_ssh_public_key.strip()]
        )
        # TODO: Setup jump pod in a separate thread to avoid long-running run_job
        _setup_jump_pod(
            api=self.api,
            project_name=run.project_name,
            project_ssh_public_key=project_ssh_public_key.strip(),
            project_ssh_private_key=project_ssh_private_key.strip(),
            user_ssh_public_key=run.run_spec.ssh_key_pub.strip(),
            jump_pod_host=self.config.networking.ssh_host,
            jump_pod_port=self.config.networking.ssh_port,
        )
        response = self.api.create_namespaced_pod(
            namespace="default",
            body=client.V1Pod(
                metadata=client.V1ObjectMeta(
                    name=instance_name,
                    labels={"app.kubernetes.io/name": instance_name},
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name=f"{instance_name}-container",
                            image=job.job_spec.image_name,
                            command=["/bin/sh"],
                            args=["-c", " && ".join(commands)],
                            ports=[
                                client.V1ContainerPort(
                                    container_port=10022,
                                )
                            ],
                        )
                    ]
                ),
            ),
        )
        response = self.api.create_namespaced_service(
            namespace="default",
            body=client.V1Service(
                metadata=client.V1ObjectMeta(name=f"{instance_name}-service"),
                spec=client.V1ServiceSpec(
                    type="ClusterIP",
                    selector={"app.kubernetes.io/name": instance_name},
                    ports=[client.V1ServicePort(port=10022)],
                ),
            ),
        )
        service_ip = response.spec.cluster_ip
        return LaunchedInstanceInfo(
            instance_id=instance_name,
            ip_address=service_ip,
            region="local",
            username="root",
            ssh_port=10022,
            dockerized=False,
            ssh_proxy=SSHConnectionParams(
                hostname=self.config.networking.ssh_host,
                username="root",
                port=self.config.networking.ssh_port,
            ),
            backend_data=None,
        )

    def terminate_instance(
        self, instance_id: str, region: str, backend_data: Optional[str] = None
    ):
        pass


def _setup_jump_pod(
    api: client.CoreV1Api,
    project_name: str,
    project_ssh_public_key: str,
    project_ssh_private_key: str,
    user_ssh_public_key: str,
    jump_pod_host: str,
    jump_pod_port: int,
):
    _create_jump_pod_service_if_not_exists(
        api=api,
        project_name=project_name,
        project_ssh_public_key=project_ssh_public_key,
        jump_pod_port=jump_pod_port,
    )
    _wait_for_pod_ready(
        api=api,
        pod_name=f"{project_name}-ssh-jump-pod",
    )
    _add_authorized_key_to_jump_pod(
        jump_pod_host=jump_pod_host,
        jump_pod_port=jump_pod_port,
        ssh_private_key=project_ssh_private_key,
        ssh_authorized_key=user_ssh_public_key,
    )


def _create_jump_pod_service_if_not_exists(
    api: client.CoreV1Api,
    project_name: str,
    project_ssh_public_key: str,
    jump_pod_port: int,
):
    try:
        api.read_namespaced_service(
            name=f"{project_name}-ssh-jump-pod-service",
            namespace="default",
        )
    except client.ApiException as e:
        if e.status == 404:
            _create_jump_pod_service(
                api=api,
                project_name=project_name,
                project_ssh_public_key=project_ssh_public_key,
                jump_pod_port=jump_pod_port,
            )
        else:
            raise


def _create_jump_pod_service(
    api: client.CoreV1Api,
    project_name: str,
    project_ssh_public_key: str,
    jump_pod_port: int,
):
    # TODO use restricted ssh-forwarding-only user for jump pod instead of root.
    commands = _get_jump_pod_commands(authorized_keys=[project_ssh_public_key])
    pod_name = f"{project_name}-ssh-jump-pod"
    response = api.create_namespaced_pod(
        namespace="default",
        body=client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={"app.kubernetes.io/name": pod_name},
            ),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name=f"{pod_name}-container",
                        # TODO: Choose appropriate image for jump pod
                        image="dstackai/base:py3.11-0.4rc4",
                        command=["/bin/sh"],
                        args=["-c", " && ".join(commands)],
                        ports=[
                            client.V1ContainerPort(
                                container_port=22,
                            )
                        ],
                    )
                ]
            ),
        ),
    )
    response = api.create_namespaced_service(
        namespace="default",
        body=client.V1Service(
            metadata=client.V1ObjectMeta(name=f"{pod_name}-service"),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={"app.kubernetes.io/name": pod_name},
                ports=[
                    client.V1ServicePort(
                        port=22,
                        target_port=22,
                        node_port=jump_pod_port,
                    )
                ],
            ),
        ),
    )


def _get_jump_pod_commands(authorized_keys: List[str]) -> List[str]:
    authorized_keys_content = "\n".join(authorized_keys).strip()
    commands = [
        # prohibit password authentication
        'sed -i "s/.*PasswordAuthentication.*/PasswordAuthentication no/g" /etc/ssh/sshd_config',
        # create ssh dirs and add public key
        "mkdir -p /run/sshd ~/.ssh",
        "chmod 700 ~/.ssh",
        f"echo '{authorized_keys_content}' > ~/.ssh/authorized_keys",
        "chmod 600 ~/.ssh/authorized_keys",
        # regenerate host keys
        "rm -rf /etc/ssh/ssh_host_*",
        "ssh-keygen -A > /dev/null",
        # start sshd
        "/usr/sbin/sshd -p 22 -o PermitUserEnvironment=yes",
        "sleep infinity",
    ]
    return commands


def _wait_for_pod_ready(
    api: client.CoreV1Api,
    pod_name: str,
    namespace: str = "default",
    timeout_seconds: int = 300,
):
    start_time = time.time()

    while True:
        try:
            pod = api.read_namespaced_pod(name=pod_name, namespace=namespace)

            # Check if the pod is ready
            if pod.status.phase == "Running" and all(
                container_status.ready for container_status in pod.status.container_statuses
            ):
                return True

        except client.ApiException as e:
            if e.status == 404:
                # Pod not found, it might be initializing
                pass
            raise e

        elapsed_time = time.time() - start_time

        if elapsed_time >= timeout_seconds:
            logger.warning("Timeout waiting for pod %s to be ready.", pod_name)
            return False

        # Sleep for a short interval before checking again
        time.sleep(5)


def _add_authorized_key_to_jump_pod(
    jump_pod_host: str,
    jump_pod_port: int,
    ssh_private_key: str,
    ssh_authorized_key: str,
):
    _run_ssh_command(
        hostname=jump_pod_host,
        port=jump_pod_port,
        ssh_private_key=ssh_private_key,
        command=(
            f'if grep -qvF "{ssh_authorized_key}" ~/.ssh/authorized_keys; then '
            f"echo {ssh_authorized_key} >> ~/.ssh/authorized_keys; "
            "fi"
        ),
    )


def _run_ssh_command(hostname: str, port: int, ssh_private_key: str, command: str):
    with tempfile.NamedTemporaryFile("w+", 0o600) as f:
        f.write(ssh_private_key)
        f.flush()
        subprocess.run(
            [
                "ssh",
                "-o",
                "StrictHostKeyChecking=no",
                "-i",
                f.name,
                "-p",
                str(port),
                f"root@{hostname}",
                command,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
