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

"""Integration tests for the S3 Files MountTarget resource.

Tests cover the full MountTarget lifecycle:
- Create with required fields (fileSystemID, subnetID)
- Wait for available status (ACK.ResourceSynced=True)
- Verify status fields populated (id, status, networkInterfaceID, vpcID, availabilityZoneID)
- Update securityGroups, verify UpdateMountTarget called
- Delete MountTarget, verify cleanup

Prerequisites (bootstrapped automatically):
- S3 Files FileSystem in available state
- VPC with a public subnet
- Two security groups for update testing
"""

import pytest
import time
import logging

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from acktest.k8s import condition
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_s3files_resource
from e2e.replacement_values import REPLACEMENT_VALUES
from e2e.bootstrap_resources import get_bootstrap_resources

RESOURCE_PLURAL = "mounttargets"

# MountTarget creation is async — initial wait before polling
CREATE_WAIT_AFTER_SECONDS = 10

# Max wait for mount target to reach available (up to 10 minutes)
AVAILABLE_WAIT_PERIODS = 30
AVAILABLE_WAIT_PERIOD_LENGTH = 20  # seconds per period


def _get_replacements():
    """Build replacement values from bootstrap resources."""
    replacements = REPLACEMENT_VALUES.copy()
    return replacements


def _wait_for_mount_target_available(ref, wait_periods=AVAILABLE_WAIT_PERIODS):
    """Wait for the MountTarget to reach available status via the Synced condition."""
    return k8s.wait_on_condition(
        ref,
        condition.CONDITION_TYPE_RESOURCE_SYNCED,
        "True",
        wait_periods=wait_periods,
        period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
    )


def _get_mount_target_status_field(ref, field):
    """Get a field from the MountTarget CR status."""
    cr = k8s.get_resource(ref)
    if cr is None:
        return None
    return cr.get("status", {}).get(field)


def _get_mount_target_id(ref):
    """Get the id from the CR status."""
    return _get_mount_target_status_field(ref, "id")


def _get_aws_mount_target(s3files_client, mount_target_id):
    """Get the mount target from AWS using the S3 Files API."""
    try:
        return s3files_client.get_mount_target(mountTargetId=mount_target_id)
    except s3files_client.exceptions.ResourceNotFoundException:
        return None


def _get_aws_mount_target_security_groups(s3files_client, mount_target_id):
    """Get the security groups for a mount target from AWS."""
    try:
        resp = s3files_client.get_mount_target(
            mountTargetId=mount_target_id,
        )
        return resp.get("securityGroups", [])
    except s3files_client.exceptions.ResourceNotFoundException:
        return None


@pytest.fixture(scope="module")
def simple_mount_target(s3files_client):
    """Create a simple MountTarget for basic lifecycle tests."""
    resource_name = random_suffix_name("ack-s3files-mt", 24)

    replacements = _get_replacements()
    replacements["MOUNT_TARGET_NAME"] = resource_name
    replacements["SUBNET_ID"] = REPLACEMENT_VALUES["SUBNET_ID"]

    resource_data = load_s3files_resource(
        "mount_target",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    # Teardown
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except Exception:
        pass


@pytest.fixture(scope="module")
def mount_target_with_sg(s3files_client):
    """Create a MountTarget with a security group for update tests."""
    resource_name = random_suffix_name("ack-s3files-mt-sg", 24)

    replacements = _get_replacements()
    replacements["MOUNT_TARGET_NAME"] = resource_name
    replacements["SUBNET_ID"] = REPLACEMENT_VALUES["SUBNET_ID_2"]

    resource_data = load_s3files_resource(
        "mount_target_with_sg",
        additional_replacements=replacements,
    )
    logging.debug(resource_data)

    ref = k8s.CustomResourceReference(
        CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
        resource_name, namespace="default",
    )
    k8s.create_custom_resource(ref, resource_data)
    cr = k8s.wait_resource_consumed_by_controller(ref)

    assert cr is not None
    assert k8s.get_resource_exists(ref)

    yield (ref, cr)

    # Teardown
    try:
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted
    except Exception:
        pass


@service_marker
@pytest.mark.canary
class TestMountTarget:
    """E2E tests for the S3 Files MountTarget resource lifecycle."""

    def test_create_and_wait_for_available(self, s3files_client, simple_mount_target):
        """Test that creating a MountTarget CR invokes CreateMountTarget and reaches available."""
        (ref, cr) = simple_mount_target

        # Wait for the mount target to become available
        assert _wait_for_mount_target_available(ref), \
            "MountTarget did not reach available status (ACK.ResourceSynced=True)"
        condition.assert_synced(ref)

        # Verify status fields are populated
        cr = k8s.get_resource(ref)
        assert cr is not None

        status = cr.get("status", {})
        assert status.get("id") is not None, "id not populated"
        assert status.get("status") == "available", \
            f"Expected available, got {status.get('status')}"
        assert status.get("networkInterfaceID") is not None, "networkInterfaceID not populated"
        assert status.get("vpcID") is not None, "vpcID not populated"
        assert status.get("availabilityZoneID") is not None, "availabilityZoneID not populated"

        # Verify the mount target exists in AWS
        mount_target_id = status["id"]
        aws_mt = _get_aws_mount_target(s3files_client, mount_target_id)
        assert aws_mt is not None, "MountTarget not found in AWS"

    def test_update_security_groups(self, s3files_client, mount_target_with_sg):
        """Test that updating securityGroups triggers UpdateMountTarget API."""
        (ref, cr) = mount_target_with_sg

        # Wait for the mount target to become available
        assert _wait_for_mount_target_available(ref), \
            "MountTarget did not reach available status before update test"
        condition.assert_synced(ref)

        mount_target_id = _get_mount_target_id(ref)
        assert mount_target_id is not None, "id not populated"

        # Patch securityGroups to use SG2 instead of SG1
        resources = get_bootstrap_resources()
        new_sg = resources.MountTargetSecurityGroup2ID
        updates = {"spec": {"securityGroups": [new_sg]}}
        k8s.patch_custom_resource(ref, updates)
        time.sleep(CREATE_WAIT_AFTER_SECONDS)

        # Wait for the controller to reconcile and reach Synced=True
        assert _wait_for_mount_target_available(ref), \
            "MountTarget did not reach Synced=True after security group update"
        condition.assert_synced(ref)

        # Verify AWS reflects the new security group
        aws_sgs = _get_aws_mount_target_security_groups(s3files_client, mount_target_id)
        assert aws_sgs is not None, "Failed to get security groups from AWS"
        assert new_sg in aws_sgs, \
            f"Expected SG {new_sg} in AWS security groups, got {aws_sgs}"

    def test_delete_mount_target(self, s3files_client):
        """Test that deleting the CR invokes DeleteMountTarget and cleans up."""
        resource_name = random_suffix_name("ack-s3files-mt-del", 24)

        replacements = _get_replacements()
        replacements["MOUNT_TARGET_NAME"] = resource_name
        replacements["SUBNET_ID"] = REPLACEMENT_VALUES["SUBNET_ID_3"]

        resource_data = load_s3files_resource(
            "mount_target",
            additional_replacements=replacements,
        )

        ref = k8s.CustomResourceReference(
            CRD_GROUP, CRD_VERSION, RESOURCE_PLURAL,
            resource_name, namespace="default",
        )
        k8s.create_custom_resource(ref, resource_data)
        cr = k8s.wait_resource_consumed_by_controller(ref)
        assert cr is not None

        # Wait for available
        assert _wait_for_mount_target_available(ref), \
            "MountTarget did not reach available before deletion test"
        condition.assert_synced(ref)

        cr = k8s.get_resource(ref)
        mount_target_id = cr["status"]["id"]

        # Verify mount target exists in AWS
        aws_mt = _get_aws_mount_target(s3files_client, mount_target_id)
        assert aws_mt is not None

        # Delete the K8s resource
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted

        # Wait for AWS deletion to complete
        max_attempts = 30
        wait_seconds = 20

        for _ in range(max_attempts):
            time.sleep(wait_seconds)
            aws_mt = _get_aws_mount_target(s3files_client, mount_target_id)
            if aws_mt is None:
                return
            lifecycle = aws_mt.get("status", "")
            if lifecycle.lower() == "deleted":
                return

        pytest.fail(
            f"MountTarget {mount_target_id} was not deleted from AWS after "
            f"{max_attempts * wait_seconds} seconds"
        )
