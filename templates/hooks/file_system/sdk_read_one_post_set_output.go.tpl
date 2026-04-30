
	// --- Fetch FileSystemPolicy sub-resource ---
	{
		policy, policyErr := rm.fetchFileSystemPolicy(ctx, ko.Status.FileSystemID)
		if policyErr != nil {
			return nil, policyErr
		}
		ko.Spec.Policy = policy
	}

	// --- Fetch SynchronizationConfiguration sub-resource ---
	{
		importRules, expirationRules, latestVersion, syncErr := rm.fetchSynchronizationConfiguration(ctx, ko.Status.FileSystemID)
		if syncErr != nil {
			return nil, syncErr
		}
		ko.Spec.ImportDataRules = importRules
		ko.Spec.ExpirationDataRules = expirationRules
		ko.Status.LatestVersionNumber = latestVersion
	}

	// --- Terminal condition on error lifecycle state ---
	// Per AWS docs, the ERROR state means "The file system is in a failed
	// state and is unrecoverable." The user must delete and recreate the
	// resource — there is no way to transition out of the error state.
	if ko.Status.Status != nil && *ko.Status.Status == "error" {
		msg := "FileSystem is in error state"
		if ko.Status.StatusMessage != nil {
			msg = *ko.Status.StatusMessage
		}
		ackcondition.SetTerminal(&resource{ko}, corev1.ConditionTrue, &msg, nil)
	} else {
		ackcondition.SetTerminal(&resource{ko}, corev1.ConditionFalse, nil, nil)
	}
