# Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"). You may
# not use this file except in compliance with the License. A copy of the
# License is located at
#
#	 http://aws.amazon.com/apache2.0/
#
# or in the "license" file accompanying this file. This file is distributed
# on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
# express or implied. See the License for the specific language governing
# permissions and limitations under the License.
"""Bootstraps the resources required to run the S3Files integration tests.
"""
import logging
import time
import boto3

from acktest.bootstrapping import Resources, BootstrapFailureException
from acktest.bootstrapping.s3 import Bucket
from acktest.bootstrapping.iam import Role
from acktest.bootstrapping.vpc import VPC

from e2e import bootstrap_directory
from e2e.bootstrap_resources import BootstrapResources

# S3 Files is built on EFS technology, so the role must trust the
# elasticfilesystem.amazonaws.com service principal (not s3files).
# The role needs S3 bucket access plus EventBridge permissions for
# change detection between the file system and the S3 bucket.
S3FILES_ROLE_POLICIES = [
    "arn:aws:iam::aws:policy/AmazonS3FullAccess",
    "arn:aws:iam::aws:policy/AmazonEventBridgeFullAccess",
]

# FileSystem polling constants
FS_AVAILABLE_WAIT_PERIODS = 30
FS_AVAILABLE_WAIT_PERIOD_LENGTH = 20  # seconds


def _enable_bucket_versioning(bucket_name: str):
    """S3 Files requires versioning enabled on the backing bucket."""
    s3 = boto3.client("s3")
    s3.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )
    logging.info(f"Enabled versioning on bucket {bucket_name}")


def _get_bucket_arn(bucket_name: str) -> str:
    """Construct the S3 bucket ARN from the bucket name."""
    return f"arn:aws:s3:::{bucket_name}"


def _create_security_group(ec2_client, vpc_id: str, name: str) -> str:
    """Create a security group in the given VPC and return its ID."""
    resp = ec2_client.create_security_group(
        GroupName=name,
        Description=f"ACK s3files e2e test SG: {name}",
        VpcId=vpc_id,
    )
    sg_id = resp["GroupId"]
    logging.info(f"Created security group {sg_id} ({name}) in VPC {vpc_id}")
    return sg_id


def _create_mount_target_file_system(bucket_arn: str, role_arn: str) -> str:
    """Create an S3 Files FileSystem and return its ID."""
    s3files = boto3.client("s3files")
    resp = s3files.create_file_system(
        bucket=bucket_arn,
        roleArn=role_arn,
    )
    fs_id = resp["fileSystemId"]
    logging.info(f"Created S3 Files FileSystem {fs_id}")
    return fs_id


def _wait_for_file_system_available(file_system_id: str):
    """Poll the FileSystem until it reaches 'available' status."""
    s3files = boto3.client("s3files")
    for i in range(FS_AVAILABLE_WAIT_PERIODS):
        resp = s3files.get_file_system(fileSystemId=file_system_id)
        status = resp.get("lifeCycleState", resp.get("status", ""))
        logging.info(
            f"FileSystem {file_system_id} status: {status} "
            f"(poll {i+1}/{FS_AVAILABLE_WAIT_PERIODS})"
        )
        if status.lower() == "available":
            logging.info(f"FileSystem {file_system_id} is available")
            return
        time.sleep(FS_AVAILABLE_WAIT_PERIOD_LENGTH)
    raise RuntimeError(
        f"FileSystem {file_system_id} did not reach 'available' status "
        f"after {FS_AVAILABLE_WAIT_PERIODS * FS_AVAILABLE_WAIT_PERIOD_LENGTH}s"
    )


def service_bootstrap() -> Resources:
    logging.getLogger().setLevel(logging.INFO)

    resources = BootstrapResources(
        FileSystemBucket=Bucket(
            "ack-s3files-e2e-bucket",
        ),
        FileSystemRole=Role(
            "ack-s3files-e2e-role",
            principal_service="elasticfilesystem.amazonaws.com",
            managed_policies=S3FILES_ROLE_POLICIES,
        ),
        MountTargetVPC=VPC(
            name_prefix="ack-s3files-mt-vpc",
            num_public_subnet=3,
            num_private_subnet=0,
        ),
    )

    try:
        resources.bootstrap()
        _enable_bucket_versioning(resources.FileSystemBucket.name)
    except BootstrapFailureException as ex:
        exit(254)

    # Create security groups in the MountTarget VPC
    ec2_client = boto3.client("ec2")
    vpc_id = resources.MountTargetVPC.vpc_id

    resources.MountTargetSecurityGroup1ID = _create_security_group(
        ec2_client, vpc_id, "ack-s3files-mt-sg1",
    )
    resources.MountTargetSecurityGroup2ID = _create_security_group(
        ec2_client, vpc_id, "ack-s3files-mt-sg2",
    )

    # Create an S3 Files FileSystem for MountTarget tests
    bucket_arn = _get_bucket_arn(resources.FileSystemBucket.name)
    role_arn = resources.FileSystemRole.arn

    fs_id = _create_mount_target_file_system(bucket_arn, role_arn)
    _wait_for_file_system_available(fs_id)
    resources.MountTargetFileSystemID = fs_id

    return resources

if __name__ == "__main__":
    config = service_bootstrap()
    # Write config to current directory by default
    config.serialize(bootstrap_directory)
