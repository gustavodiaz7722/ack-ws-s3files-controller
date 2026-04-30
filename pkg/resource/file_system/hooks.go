// Copyright Amazon.com Inc. or its affiliates. All Rights Reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License"). You may
// not use this file except in compliance with the License. A copy of the
// License is located at
//
//     http://aws.amazon.com/apache2.0/
//
// or in the "license" file accompanying this file. This file is distributed
// on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
// express or implied. See the License for the specific language governing
// permissions and limitations under the License.

package file_system

import (
	"context"
	"errors"
	"fmt"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackrequeue "github.com/aws-controllers-k8s/runtime/pkg/requeue"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	svcsdk "github.com/aws/aws-sdk-go-v2/service/s3files"
	svcsdktypes "github.com/aws/aws-sdk-go-v2/service/s3files/types"
	smithy "github.com/aws/smithy-go"

	svcapitypes "github.com/aws-controllers-k8s/s3files-controller/apis/v1alpha1"
	svctags "github.com/aws-controllers-k8s/s3files-controller/pkg/tags"
)

var syncTags = svctags.Tags

// customUpdateFileSystem handles updates for FileSystem resources.
// There is no UpdateFileSystem API — all updates go through sub-resource
// APIs (Policy, SynchronizationConfiguration) and tag sync.
func (rm *resourceManager) customUpdateFileSystem(
	ctx context.Context,
	desired *resource,
	latest *resource,
	delta *ackcompare.Delta,
) (*resource, error) {
	var err error

	// Handle tag changes first — TagResource/UntagResource are metadata
	// operations that do not require the FileSystem to be in available state.
	if delta.DifferentAt("Spec.Tags") {
		err = syncTags(
			ctx,
			desired.ko.Spec.Tags, latest.ko.Spec.Tags,
			latest.ko.Status.FileSystemID, convertToOrderedACKTags,
			rm.sdkapi, rm.metrics,
		)
		if err != nil {
			return nil, err
		}
	}

	// Check if any sub-resource fields changed (Policy, SyncConfig).
	// If only tags changed, we're done — no need to wait for available state.
	subResourceChanged := delta.DifferentAt("Spec.Policy") ||
		delta.DifferentAt("Spec.ImportDataRules") ||
		delta.DifferentAt("Spec.ExpirationDataRules")

	if !subResourceChanged {
		return desired, nil
	}

	// Guard: Do not attempt sub-resource updates while the FileSystem is in
	// a transitional state. Sub-resource APIs require the FileSystem to be
	// available.
	if latest.ko.Status.Status != nil {
		latestState := *latest.ko.Status.Status
		if latestState != "available" {
			return nil, ackrequeue.NeededAfter(
				fmt.Errorf("FileSystem is in state '%s', cannot update sub-resources", latestState),
				ackrequeue.DefaultRequeueAfterDuration,
			)
		}
	}

	// Handle Policy changes via PutFileSystemPolicy / DeleteFileSystemPolicy
	if delta.DifferentAt("Spec.Policy") {
		desiredPolicy := ""
		if desired.ko.Spec.Policy != nil {
			desiredPolicy = *desired.ko.Spec.Policy
		}
		if desiredPolicy != "" {
			_, err = rm.sdkapi.PutFileSystemPolicy(ctx, &svcsdk.PutFileSystemPolicyInput{
				FileSystemId: latest.ko.Status.FileSystemID,
				Policy:       &desiredPolicy,
			})
			rm.metrics.RecordAPICall("UPDATE", "PutFileSystemPolicy", err)
			if err != nil {
				return nil, err
			}
		} else {
			// Desired policy is empty — remove the existing policy.
			_, err = rm.sdkapi.DeleteFileSystemPolicy(ctx, &svcsdk.DeleteFileSystemPolicyInput{
				FileSystemId: latest.ko.Status.FileSystemID,
			})
			rm.metrics.RecordAPICall("UPDATE", "DeleteFileSystemPolicy", err)
			if err != nil {
				var apiErr smithy.APIError
				if errors.As(err, &apiErr) && apiErr.ErrorCode() == "ResourceNotFoundException" {
					// No policy exists — treat as success.
				} else {
					return nil, err
				}
			}
		}
	}

	// Handle SynchronizationConfiguration changes via PutSynchronizationConfiguration
	if delta.DifferentAt("Spec.ImportDataRules") || delta.DifferentAt("Spec.ExpirationDataRules") {
		if desired.ko.Spec.ImportDataRules != nil || desired.ko.Spec.ExpirationDataRules != nil {
			putInput := &svcsdk.PutSynchronizationConfigurationInput{
				FileSystemId: latest.ko.Status.FileSystemID,
			}
			// Pass latestVersionNumber for optimistic concurrency
			if latest.ko.Status.LatestVersionNumber != nil {
				v := int32(*latest.ko.Status.LatestVersionNumber)
				putInput.LatestVersionNumber = &v
			}
			if desired.ko.Spec.ImportDataRules != nil {
				sdkRules := make([]svcsdktypes.ImportDataRule, len(desired.ko.Spec.ImportDataRules))
				for i, r := range desired.ko.Spec.ImportDataRules {
					sdkRule := svcsdktypes.ImportDataRule{}
					if r.Prefix != nil {
						sdkRule.Prefix = r.Prefix
					}
					if r.SizeLessThan != nil {
						sdkRule.SizeLessThan = r.SizeLessThan
					}
					if r.Trigger != nil {
						sdkRule.Trigger = svcsdktypes.ImportTrigger(*r.Trigger)
					}
					sdkRules[i] = sdkRule
				}
				putInput.ImportDataRules = sdkRules
			}
			if desired.ko.Spec.ExpirationDataRules != nil {
				sdkRules := make([]svcsdktypes.ExpirationDataRule, len(desired.ko.Spec.ExpirationDataRules))
				for i, r := range desired.ko.Spec.ExpirationDataRules {
					sdkRule := svcsdktypes.ExpirationDataRule{}
					if r.DaysAfterLastAccess != nil {
						d := int32(*r.DaysAfterLastAccess)
						sdkRule.DaysAfterLastAccess = &d
					}
					sdkRules[i] = sdkRule
				}
				putInput.ExpirationDataRules = sdkRules
			}
			_, err = rm.sdkapi.PutSynchronizationConfiguration(ctx, putInput)
			rm.metrics.RecordAPICall("UPDATE", "PutSynchronizationConfiguration", err)
			if err != nil {
				return nil, err
			}
		} else {
			// User removed both importDataRules and expirationDataRules.
			// There is no DeleteSynchronizationConfiguration API, so the
			// existing config remains on the AWS side.
			rlog := ackrtlog.FromContext(ctx)
			rlog.Info(
				"SynchronizationConfiguration cannot be deleted via the S3 Files API. " +
					"The existing configuration will remain on the AWS resource.",
			)
		}
	}

	return desired, nil
}

// fetchFileSystemPolicy retrieves the FileSystem policy sub-resource.
// Returns nil policy if no policy exists or the file system is not yet available.
func (rm *resourceManager) fetchFileSystemPolicy(
	ctx context.Context,
	fileSystemID *string,
) (*string, error) {
	if fileSystemID == nil {
		return nil, nil
	}
	policyResp, err := rm.sdkapi.GetFileSystemPolicy(ctx, &svcsdk.GetFileSystemPolicyInput{
		FileSystemId: fileSystemID,
	})
	rm.metrics.RecordAPICall("READ_ONE", "GetFileSystemPolicy", err)
	if err != nil {
		var apiErr smithy.APIError
		if errors.As(err, &apiErr) && (apiErr.ErrorCode() == "ResourceNotFoundException" || apiErr.ErrorCode() == "ValidationException") {
			// ResourceNotFoundException: no policy exists.
			// ValidationException: file system is not yet available.
			return nil, nil
		}
		return nil, err
	}
	return policyResp.Policy, nil
}

// fetchSynchronizationConfiguration retrieves the SynchronizationConfiguration
// sub-resource. Returns nil fields if no config exists or the file system is
// not yet available.
func (rm *resourceManager) fetchSynchronizationConfiguration(
	ctx context.Context,
	fileSystemID *string,
) ([]*svcapitypes.ImportDataRule, []*svcapitypes.ExpirationDataRule, *int64, error) {
	if fileSystemID == nil {
		return nil, nil, nil, nil
	}
	syncResp, err := rm.sdkapi.GetSynchronizationConfiguration(ctx, &svcsdk.GetSynchronizationConfigurationInput{
		FileSystemId: fileSystemID,
	})
	rm.metrics.RecordAPICall("READ_ONE", "GetSynchronizationConfiguration", err)
	if err != nil {
		var apiErr smithy.APIError
		if errors.As(err, &apiErr) && (apiErr.ErrorCode() == "ResourceNotFoundException" || apiErr.ErrorCode() == "ValidationException") {
			// ResourceNotFoundException: no sync config exists.
			// ValidationException: file system is not yet available.
			return nil, nil, nil, nil
		}
		return nil, nil, nil, err
	}

	var importRules []*svcapitypes.ImportDataRule
	if syncResp.ImportDataRules != nil {
		importRules = make([]*svcapitypes.ImportDataRule, len(syncResp.ImportDataRules))
		for i, r := range syncResp.ImportDataRules {
			rule := &svcapitypes.ImportDataRule{}
			if r.Prefix != nil {
				rule.Prefix = r.Prefix
			}
			if r.SizeLessThan != nil {
				rule.SizeLessThan = r.SizeLessThan
			}
			if r.Trigger != "" {
				trigger := string(r.Trigger)
				rule.Trigger = &trigger
			}
			importRules[i] = rule
		}
	}

	var expirationRules []*svcapitypes.ExpirationDataRule
	if syncResp.ExpirationDataRules != nil {
		expirationRules = make([]*svcapitypes.ExpirationDataRule, len(syncResp.ExpirationDataRules))
		for i, r := range syncResp.ExpirationDataRules {
			rule := &svcapitypes.ExpirationDataRule{}
			if r.DaysAfterLastAccess != nil {
				d := int64(*r.DaysAfterLastAccess)
				rule.DaysAfterLastAccess = &d
			}
			expirationRules[i] = rule
		}
	}

	var latestVersion *int64
	if syncResp.LatestVersionNumber != nil {
		v := int64(*syncResp.LatestVersionNumber)
		latestVersion = &v
	}

	return importRules, expirationRules, latestVersion, nil
}
