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

"""Integration tests for the S3 Files AccessPoint resource.

Tests cover the full AccessPoint lifecycle:
- Create with required fields (fileSystemID)
- Wait for available status (ACK.ResourceSynced=True)
- Verify status fields populated (id, status, ackResourceMetadata.arn)
- Create with posixUser and rootDirectory, verify round-trip
- Tag lifecycle (create-time tags, add tag, remove tag)
- Delete AccessPoint, verify cleanup via ResourceNotFoundException

Prerequisites (bootstrapped automatically):
- S3 Files FileSystem in available state (shared via SharedFileSystemID)
"""

import pytest
import time
import logging

from acktest.resources import random_suffix_name
from acktest.k8s import resource as k8s
from acktest.k8s import condition
from acktest import tags as acktags
from e2e import service_marker, CRD_GROUP, CRD_VERSION, load_s3files_resource
from e2e.replacement_values import REPLACEMENT_VALUES

RESOURCE_PLURAL = "accesspoints"

# AccessPoint creation is async — initial wait before polling
CREATE_WAIT_AFTER_SECONDS = 10
UPDATE_WAIT_AFTER_SECONDS = 10

# Max wait for access point to reach available (up to 10 minutes)
AVAILABLE_WAIT_PERIODS = 30
AVAILABLE_WAIT_PERIOD_LENGTH = 20  # seconds per period


def _get_replacements():
    """Build replacement values from bootstrap resources."""
    replacements = REPLACEMENT_VALUES.copy()
    return replacements


def _get_aws_access_point(s3files_client, access_point_id):
    """Get the access point from AWS using the S3 Files API.

    Returns None when the resource does not exist.
    """
    try:
        return s3files_client.get_access_point(accessPointId=access_point_id)
    except s3files_client.exceptions.ResourceNotFoundException:
        return None


def _get_aws_access_point_tags(s3files_client, access_point_id):
    """Get the tags for an access point as a {key: value} dict.

    Returns None when the resource does not exist.
    """
    try:
        resp = s3files_client.list_tags_for_resource(resourceId=access_point_id)
    except s3files_client.exceptions.ResourceNotFoundException:
        return None
    return {t["key"]: t["value"] for t in resp.get("tags", [])}


@pytest.fixture(scope="module")
def simple_access_point(s3files_client):
    """Create a simple AccessPoint for basic lifecycle tests."""
    resource_name = random_suffix_name("ack-s3files-ap", 24)

    replacements = _get_replacements()
    replacements["ACCESS_POINT_NAME"] = resource_name

    resource_data = load_s3files_resource(
        "access_point",
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
def posix_access_point(s3files_client):
    """Create an AccessPoint with posixUser and rootDirectory."""
    resource_name = random_suffix_name("ack-s3files-ap-posix", 24)

    replacements = _get_replacements()
    replacements["ACCESS_POINT_NAME"] = resource_name

    resource_data = load_s3files_resource(
        "access_point_with_posix",
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
def tagged_access_point(s3files_client):
    """Create an AccessPoint with two initial tags for tag lifecycle tests."""
    resource_name = random_suffix_name("ack-s3files-ap-tags", 24)

    replacements = _get_replacements()
    replacements["ACCESS_POINT_NAME"] = resource_name

    resource_data = load_s3files_resource(
        "access_point_with_tags",
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
class TestAccessPoint:
    """E2E tests for the S3 Files AccessPoint resource lifecycle."""

    def test_create_and_wait_for_available(self, s3files_client, simple_access_point):
        """Test that creating an AccessPoint CR reaches available and has all status fields."""
        (ref, _) = simple_access_point

        # Wait for the access point to become available
        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach available status (ACK.ResourceSynced=True)"

        # Verify status fields are populated
        cr = k8s.get_resource(ref)
        assert cr is not None

        status = cr.get("status", {})
        assert status.get("status") == "available", \
            f"Expected available, got {status.get('status')}"
        assert status.get("id") is not None, "id not populated"

        # Verify ACK resource metadata has the ARN
        ack_metadata = status.get("ackResourceMetadata", {})
        assert ack_metadata.get("arn") is not None, "ARN not populated in ackResourceMetadata"

        # Cross-verify with AWS
        access_point_id = status["id"]
        aws_ap = _get_aws_access_point(s3files_client, access_point_id)
        assert aws_ap is not None, "AccessPoint not found in AWS"

        # Verify the ARN in CR status matches the AccessPointArn from AWS
        assert ack_metadata["arn"] == aws_ap["accessPointArn"], \
            f"CR ARN {ack_metadata['arn']} does not match AWS AccessPointArn {aws_ap['accessPointArn']}"

    def test_posix_and_root_directory(self, s3files_client, posix_access_point):
        """Test that posixUser and rootDirectory fields round-trip via GetAccessPoint."""
        (ref, _) = posix_access_point

        # Wait for the access point to become available
        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach available status"

        cr = k8s.get_resource(ref)
        access_point_id = cr["status"]["id"]
        assert access_point_id is not None

        aws_ap = _get_aws_access_point(s3files_client, access_point_id)
        assert aws_ap is not None, "AccessPoint not found in AWS"

        # PosixUser exact-equality checks
        assert aws_ap["posixUser"]["uid"] == 1000
        assert aws_ap["posixUser"]["gid"] == 100

        # RootDirectory exact-equality checks
        assert aws_ap["rootDirectory"]["path"] == "/tenant-a"
        assert aws_ap["rootDirectory"]["creationPermissions"]["ownerUid"] == 1000
        assert aws_ap["rootDirectory"]["creationPermissions"]["ownerGid"] == 100
        assert aws_ap["rootDirectory"]["creationPermissions"]["permissions"] == "0755"

    def test_tag_lifecycle(self, s3files_client, tagged_access_point):
        """Test create-time tags, adding a tag, and removing a tag (single combined test)."""
        (ref, _) = tagged_access_point

        # Wait for the access point to become available
        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach available status"

        cr = k8s.get_resource(ref)
        access_point_id = cr["status"]["id"]
        assert access_point_id is not None

        # --- Phase 1: Verify create-time tags ---
        aws_tags = _get_aws_access_point_tags(s3files_client, access_point_id)
        assert aws_tags is not None, "Failed to list tags for AccessPoint"

        # Verify default controller tags are injected by EnsureTags()
        acktags.assert_ack_system_tags(aws_tags)

        # Verify user tags (excluding system tags)
        acktags.assert_equal_without_ack_tags(
            expected={"Environment": "testing", "ManagedBy": "ACK"},
            actual=aws_tags,
        )

        # --- Phase 2: Add a new tag (Environment, ManagedBy, NewTag) ---
        new_tags = [
            {"key": "Environment", "value": "testing"},
            {"key": "ManagedBy", "value": "ACK"},
            {"key": "NewTag", "value": "new-value"},
        ]
        updates = {"spec": {"tags": new_tags}}
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach Synced=True after tag add"

        # Poll AWS until the new tag appears
        max_tag_polls = 15
        for _ in range(max_tag_polls):
            aws_tags = _get_aws_access_point_tags(s3files_client, access_point_id)
            if aws_tags is not None and "NewTag" in aws_tags:
                break
            time.sleep(AVAILABLE_WAIT_PERIOD_LENGTH)
        else:
            pytest.fail(
                f"Tag add did not propagate after "
                f"{max_tag_polls * AVAILABLE_WAIT_PERIOD_LENGTH}s. "
                f"Current tags: {aws_tags}"
            )

        acktags.assert_equal_without_ack_tags(
            expected={"Environment": "testing", "ManagedBy": "ACK", "NewTag": "new-value"},
            actual=aws_tags,
        )

        # --- Phase 3: Remove ManagedBy (leaving Environment + NewTag) ---
        reduced_tags = [
            {"key": "Environment", "value": "testing"},
            {"key": "NewTag", "value": "new-value"},
        ]
        updates = {"spec": {"tags": reduced_tags}}
        k8s.patch_custom_resource(ref, updates)
        time.sleep(UPDATE_WAIT_AFTER_SECONDS)

        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach Synced=True after tag remove"

        # Poll AWS until ManagedBy disappears
        for _ in range(max_tag_polls):
            aws_tags = _get_aws_access_point_tags(s3files_client, access_point_id)
            if aws_tags is not None and "ManagedBy" not in aws_tags:
                break
            time.sleep(AVAILABLE_WAIT_PERIOD_LENGTH)
        else:
            pytest.fail(
                f"Tag remove did not propagate after "
                f"{max_tag_polls * AVAILABLE_WAIT_PERIOD_LENGTH}s. "
                f"Current tags: {aws_tags}"
            )

        acktags.assert_equal_without_ack_tags(
            expected={"Environment": "testing", "NewTag": "new-value"},
            actual=aws_tags,
        )

    def test_delete_access_point(self, s3files_client):
        """Test that deleting the CR invokes DeleteAccessPoint and cleans up."""
        resource_name = random_suffix_name("ack-s3files-ap-del", 24)

        replacements = _get_replacements()
        replacements["ACCESS_POINT_NAME"] = resource_name

        resource_data = load_s3files_resource(
            "access_point",
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
        assert k8s.wait_on_condition(
            ref,
            condition.CONDITION_TYPE_RESOURCE_SYNCED,
            "True",
            wait_periods=AVAILABLE_WAIT_PERIODS,
            period_length=AVAILABLE_WAIT_PERIOD_LENGTH,
        ), "AccessPoint did not reach available before deletion test"

        cr = k8s.get_resource(ref)
        access_point_id = cr["status"]["id"]

        # Verify access point exists in AWS
        aws_ap = _get_aws_access_point(s3files_client, access_point_id)
        assert aws_ap is not None

        # Delete the K8s resource
        _, deleted = k8s.delete_custom_resource(ref, 3, 10)
        assert deleted

        # Poll AWS until GetAccessPoint raises ResourceNotFoundException
        max_attempts = 30
        wait_seconds = 20

        for _ in range(max_attempts):
            time.sleep(wait_seconds)
            aws_ap = _get_aws_access_point(s3files_client, access_point_id)
            if aws_ap is None:
                return

        pytest.fail(
            f"AccessPoint {access_point_id} was not deleted from AWS after "
            f"{max_attempts * wait_seconds} seconds"
        )
