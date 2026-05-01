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
"""Stores the values used by each of the integration tests for replacing the
S3Files-specific test variables.
"""

from e2e.bootstrap_resources import get_bootstrap_resources

REPLACEMENT_VALUES = {
    "FILE_SYSTEM_ID": get_bootstrap_resources().MountTargetFileSystemID,
    "SUBNET_ID": get_bootstrap_resources().MountTargetVPC.public_subnets.subnet_ids[0],
    "SUBNET_ID_2": get_bootstrap_resources().MountTargetVPC.public_subnets.subnet_ids[1],
    "SUBNET_ID_3": get_bootstrap_resources().MountTargetVPC.public_subnets.subnet_ids[2],
    "SECURITY_GROUP_ID": get_bootstrap_resources().MountTargetSecurityGroup1ID,
    "SECURITY_GROUP_ID_2": get_bootstrap_resources().MountTargetSecurityGroup2ID,
}
