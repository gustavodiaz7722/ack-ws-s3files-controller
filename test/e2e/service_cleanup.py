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

"""Cleans up the resources created by the bootstrapping process.
"""

import logging
import boto3

from acktest.bootstrapping import Resources

from e2e import bootstrap_directory
from e2e.bootstrap_resources import BootstrapResources


def _delete_file_system(file_system_id: str):
    """Delete the bootstrapped S3 Files FileSystem."""
    if not file_system_id:
        return
    try:
        s3files = boto3.client("s3files")
        s3files.delete_file_system(fileSystemId=file_system_id)
        logging.info(f"Deleted S3 Files FileSystem {file_system_id}")
    except Exception as e:
        logging.error(f"Failed to delete FileSystem {file_system_id}: {e}")


def _delete_security_group(sg_id: str):
    """Delete a security group by ID."""
    if not sg_id:
        return
    try:
        ec2 = boto3.client("ec2")
        ec2.delete_security_group(GroupId=sg_id)
        logging.info(f"Deleted security group {sg_id}")
    except Exception as e:
        logging.error(f"Failed to delete security group {sg_id}: {e}")


def service_cleanup():
    logging.getLogger().setLevel(logging.INFO)

    resources = BootstrapResources.deserialize(bootstrap_directory)

    # Clean up MountTarget bootstrap resources in correct order:
    # 1. FileSystem first (before VPC/SGs)
    # 2. Security groups (before VPC)
    # 3. VPC/subnet cleanup is handled automatically by the VPC helper
    _delete_file_system(resources.MountTargetFileSystemID)
    _delete_security_group(resources.MountTargetSecurityGroup1ID)
    _delete_security_group(resources.MountTargetSecurityGroup2ID)

    # Clean up all acktest-managed resources (Bucket, Role, VPC)
    resources.cleanup()

if __name__ == "__main__":
    service_cleanup()
