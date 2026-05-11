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

package access_point

import (
	"context"

	ackcompare "github.com/aws-controllers-k8s/runtime/pkg/compare"
	ackcondition "github.com/aws-controllers-k8s/runtime/pkg/condition"
	ackrtlog "github.com/aws-controllers-k8s/runtime/pkg/runtime/log"
	corev1 "k8s.io/api/core/v1"

	svcapitypes "github.com/aws-controllers-k8s/s3files-controller/apis/v1alpha1"
	svctags "github.com/aws-controllers-k8s/s3files-controller/pkg/tags"
)

// syncTags delegates to the shared tag-sync helper in pkg/tags. It is assigned
// as a package-level variable (matching the FileSystem pattern) so tests can
// override it if needed.
var syncTags = svctags.Tags

// customUpdateAccessPoint handles updates for AccessPoint resources.
// There is no UpdateAccessPoint API — only tag changes can be applied via
// TagResource / UntagResource. All other spec changes require
// delete-and-recreate, so drift in those fields is logged but not acted upon.
func (rm *resourceManager) customUpdateAccessPoint(
	ctx context.Context,
	desired *resource,
	latest *resource,
	delta *ackcompare.Delta,
) (*resource, error) {
	var err error

	// Tag changes are metadata operations that do not require the resource
	// to be in any particular lifecycle state.
	if delta.DifferentAt("Spec.Tags") {
		err = syncTags(
			ctx,
			desired.ko.Spec.Tags, latest.ko.Spec.Tags,
			latest.ko.Status.ID, convertToOrderedACKTags,
			rm.sdkapi, rm.metrics,
		)
		if err != nil {
			return nil, err
		}
	}

	// Surface drift in any non-tag spec field as a Terminal condition.
	// AWS provides no UpdateAccessPoint API, so the controller cannot
	// reconcile these fields — the user must delete and recreate.
	// Terminal stops pointless requeues; a spec revert by the user triggers
	// a fresh reconcile that clears the condition.
	if delta.DifferentAt("Spec.FileSystemID") ||
		delta.DifferentAt("Spec.PosixUser") ||
		delta.DifferentAt("Spec.RootDirectory") {
		msg := "Spec drift detected in immutable field(s). " +
			"S3 Files provides no UpdateAccessPoint API — to change " +
			"FileSystemId, PosixUser, or RootDirectory, delete and " +
			"recreate the resource."
		rlog := ackrtlog.FromContext(ctx)
		rlog.Info(msg)
		ackcondition.SetTerminal(desired, corev1.ConditionTrue, &msg, nil)
	}

	return desired, nil
}

// Ensure import is used (svcapitypes is used by the generated resource type).
var _ = &svcapitypes.AccessPoint{}
