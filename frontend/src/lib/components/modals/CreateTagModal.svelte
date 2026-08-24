<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import BaseModal from './BaseModal.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button } from '$lib/components/ui';
	import {
		createConfirmSettlementGate,
		getConfirmKeyboardAction,
		settleIfEligible
	} from './confirmKeyboard';

	// "Create tag" dialog, shared by every page that owns its own tag
	// vocabulary - the caller supplies the create call and the sentence that
	// explains what its tags are for.
	export let onClose: () => void;
	export let onCreate: (name: string) => Promise<{ success: boolean }>;
	export let description: string;

	let newTagName = '';
	let creatingTag = false;

	const settlementGate = createConfirmSettlementGate();

	async function handleCreateTag() {
		if (!newTagName.trim() || creatingTag) return;

		try {
			creatingTag = true;
			const response = await onCreate(newTagName.trim());
			if (response.success) {
				newTagName = '';
				onClose();
			} else {
				settlementGate.reset();
			}
		} catch (error) {
			logger.error('Failed to create tag:', error);
			settlementGate.reset();
		} finally {
			creatingTag = false;
		}
	}

	function handleCancel() {
		newTagName = '';
		onClose();
	}

	function submit() {
		settleIfEligible(settlementGate, !!newTagName.trim() && !creatingTag, handleCreateTag);
	}

	function dismiss() {
		settleIfEligible(settlementGate, !creatingTag, handleCancel);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (suppress) e.preventDefault();
		if (action === 'confirm') submit();
		else if (action === 'cancel') dismiss();
	}

	// The shared keyboard helper deliberately leaves Enter untouched while
	// focus sits in an editable element (input/textarea/select), so the text
	// field submits itself here rather than through the global handler above.
	function handleInputKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') submit();
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal
	isOpen={true}
	title=""
	size="sm"
	hideCloseButton
	closeable={!creatingTag}
	handleEscapeKey={false}
	labelledBy="add-tag-title"
	on:close={dismiss}
>
	<div class="p-6">
		<div class="flex items-center gap-2 mb-4">
			<Icon name="tag" className="w-5 h-5 text-fg" />
			<h3 id="add-tag-title" class="text-lg font-semibold text-fg">Create New Tag</h3>
		</div>
		<div class="mb-4">
			<label class="block text-sm text-fg-muted mb-2" for="new-tag-name">Tag Name</label>
			<input
				id="new-tag-name"
				type="text"
				class="w-full px-3 py-2 bg-surface-2 border border-line-strong text-fg rounded-lg focus:outline-none focus:ring-2 focus:ring-accent"
				placeholder="Enter tag name..."
				bind:value={newTagName}
				on:keydown={handleInputKeydown}
			/>
			<p class="text-sm text-fg-muted mt-2">{description}</p>
		</div>
		<div class="flex items-center justify-end gap-3">
			<Tooltip text="Cancel" kbd="Esc" position="top">
				<Button variant="secondary" onclick={dismiss}>Cancel</Button>
			</Tooltip>
			<Tooltip text="Create Tag" kbd="Enter" position="top">
				<Button
					variant="primary"
					disabled={!newTagName.trim() || creatingTag}
					loading={creatingTag}
					onclick={submit}
				>
					Confirm
				</Button>
			</Tooltip>
		</div>
	</div>
</BaseModal>
