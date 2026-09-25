<script lang="ts">
	import { logger } from '$lib/utils/logger';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { historyStore } from '$lib/stores/history';
	import { createEventDispatcher } from 'svelte';
	import type { Tag } from '$lib/types/history';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';
	import Icon from '$lib/components/Icon.svelte';

	export let isOpen = false;
	export let availableTags: Tag[] = [];

	const dispatch = createEventDispatcher();

	let files: FileList | null = null;
	let selectedTagIds: string[] = [];
	let uploading = false;
	let fileInput: HTMLInputElement;
	let previousOpen = false;
	const settlementGate = createConfirmSettlementGate();
	$: if (isOpen !== previousOpen) { previousOpen = isOpen; if (isOpen) settlementGate.reset(); }

	function handleClose() {
		if (!uploading) {
			files = null;
			selectedTagIds = [];
			dispatch('close');
		}
	}

	function handleCancel() {
		settlementGate.settle(handleClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !uploading && !!files && files.length > 0, handleUpload);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}

	function toggleTag(tagId: string) {
		if (selectedTagIds.includes(tagId)) {
			selectedTagIds = selectedTagIds.filter((id) => id !== tagId);
		} else {
			selectedTagIds = [...selectedTagIds, tagId];
		}
	}

	async function handleUpload() {
		if (!files || files.length === 0) {
			return;
		}

		uploading = true;
		try {
			const response = await api.uploadGenerations(Array.from(files), selectedTagIds);

			if (response.success) {
				// Reload history to show uploaded generations
				await historyStore.loadGenerations();
				dispatch('success', response);
				handleClose();
			} else {
				logger.error('Upload failed:', response.error);
				toasts.error(`Upload failed: ${response.error}`);
			}
		} catch (error) {
			logger.error('Upload error:', error);
			toasts.error('Upload failed. Please try again.');
		} finally {
			uploading = false;
		}
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<BaseModal {isOpen} title="Upload Generations" size="lg" closeable={!uploading} handleEscapeKey={false} on:close={handleCancel}>
	<svelte:fragment slot="headerIcon">
		<Icon name="upload" className="w-5 h-5 text-fg-muted flex-shrink-0" />
	</svelte:fragment>

	<div class="p-4 md:p-6">
		<p class="text-sm text-fg-muted mb-6">
			Upload images or videos to your generation history
		</p>

		<!-- Form -->
		<div class="space-y-4">
			<!-- File Input -->
			<div>
				<label for="file-upload" class="block text-sm font-medium text-fg mb-2">
					Select Files <span class="text-danger">*</span>
				</label>
				<input
					id="file-upload"
					type="file"
					accept="image/*,video/*"
					multiple
					bind:files
					bind:this={fileInput}
					class="block w-full text-sm text-fg-muted
						file:mr-4 file:py-2 file:px-4
						file:rounded file:border-0
						file:text-sm file:font-semibold
						file:bg-accent file:text-accent-contrast
						hover:file:bg-accent-hover
						cursor-pointer border border-line-strong rounded-lg"
				/>
				{#if files && files.length > 0}
					<p class="mt-2 text-sm text-fg-muted flex items-center gap-1">
						<Icon name="check" className="w-4 h-4 text-success" />
						{files.length} file{files.length > 1 ? 's' : ''} selected
					</p>
				{/if}
			</div>

			<!-- Tag Selection -->
			{#if availableTags.length > 0}
				<div>
					<span class="block text-sm font-medium text-fg mb-2">
						Tags (optional)
					</span>
					<div class="flex flex-wrap gap-2">
						{#each availableTags as tag}
							{@const selected = selectedTagIds.includes(tag.id)}
							<button
								type="button"
								class="inline-flex items-center gap-1 px-3 py-1 rounded text-sm border transition-all {selected
									? 'ring-1'
									: 'hover:opacity-80'}"
								style="background-color: {selected
									? tag.color
									: tag.color + '20'}; border-color: {tag.color}; color: {selected
									? 'white'
									: tag.color}"
								on:click={() => toggleTag(tag.id)}
							>
								{tag.name}
								{#if selected}
									<Icon name="check" className="w-4 h-4" />
								{/if}
							</button>
						{/each}
					</div>
				</div>
			{/if}
		</div>
	</div>

	<svelte:fragment slot="footer">
		<ConfirmFooter
			confirmLabel={uploading ? 'Uploading...' : 'Upload'}
			busy={uploading}
			confirmDisabled={!files || files.length === 0}
			onCancel={handleCancel}
			onConfirm={handleConfirm}
		/>
	</svelte:fragment>
</BaseModal>
