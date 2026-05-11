
	// --- Terminal condition on error lifecycle state ---
	// Per AWS S3 Files docs, the `error` state is unrecoverable — the file
	// system (and its access points) can only be replaced, not repaired.
	// Note: GetAccessPointOutput has no StatusMessage field (unlike
	// FileSystem / MountTarget), so we use a fixed message here.
	if ko.Status.Status != nil && *ko.Status.Status == "error" {
		msg := "AccessPoint is in error state"
		ackcondition.SetTerminal(&resource{ko}, corev1.ConditionTrue, &msg, nil)
	}
