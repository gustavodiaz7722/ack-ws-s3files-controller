
	// --- Terminal condition on error lifecycle state ---
	// The synced.when config handles setting Synced=False for non-available
	// states, but it does not set ACK.Terminal for the "error" lifecycle
	// state. This hook sets Terminal=True when the mount target is in error
	// state. The runtime's resetConditions clears all conditions at the
	// start of each reconciliation, so no explicit False-setting is needed.
	if ko.Status.Status != nil && *ko.Status.Status == "error" {
		msg := "MountTarget is in error state"
		if ko.Status.StatusMessage != nil {
			msg = *ko.Status.StatusMessage
		}
		ackcondition.SetTerminal(&resource{ko}, corev1.ConditionTrue, &msg, nil)
	}
