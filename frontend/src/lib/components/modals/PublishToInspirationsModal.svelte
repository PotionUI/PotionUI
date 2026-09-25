<script lang="ts">
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import BaseModal from './BaseModal.svelte';
	import ConfirmFooter from './ConfirmFooter.svelte';
	import { api } from '$lib/services/api/index';
	import { toasts } from '$lib/stores/toast';
	import { validatePublishForm } from '$lib/inspirations/publishValidation';
	import portal from '$lib/actions/portal';
	import type { GenerationHistoryItem } from '$lib/types/history';
	import { createConfirmSettlementGate, getConfirmKeyboardAction, settleIfEligible } from './confirmKeyboard';

	export let generation: GenerationHistoryItem;
	export let onClose: () => void;

	const MAX_TITLE_LENGTH = 200;

	interface FileOption {
		filename: string;
		label: string;
	}

	$: fileOptions = (generation.files ?? []).map((f, i): FileOption => {
		const filename = f.file_path.split('/').pop() || f.file_path;
		return { filename, label: `#${i + 1} · ${filename}` };
	});

	let title = '';
	let description = '';
	let selected: Record<string, boolean> = {};
	let submitting = false;
	let error: string | null = null;

	// Every output selected by default - only a multi-output generation gives
	// the checkboxes anything to change.
	let initializedFor: string | null = null;
	$: if (generation.id !== initializedFor && fileOptions.length > 0) {
		selected = Object.fromEntries(fileOptions.map((f) => [f.filename, true]));
		initializedFor = generation.id;
	}

	$: selectedFilenames = fileOptions.filter((f) => selected[f.filename]).map((f) => f.filename);

	function toggle(filename: string) {
		selected = { ...selected, [filename]: !selected[filename] };
	}

	const settlementGate = createConfirmSettlementGate();

	function handleCancel() {
		settleIfEligible(settlementGate, !submitting, onClose);
	}

	function handleConfirm() {
		settleIfEligible(settlementGate, !submitting, handleSubmit);
	}

	function handleKeydown(e: KeyboardEvent) {
		const { action, suppress } = getConfirmKeyboardAction(e);
		if (action === 'cancel') handleCancel();
		else if (action === 'confirm') handleConfirm();
		if (suppress) e.preventDefault();
	}

	function handleTitleKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') handleConfirm();
	}

	async function handleSubmit() {
		const validation = validatePublishForm({
			title,
			availableFilenames: fileOptions.map((f) => f.filename),
			selectedFilenames
		});
		if (!validation.valid) {
			error = validation.error ?? 'Invalid form.';
			settlementGate.reset();
			return;
		}
		error = null;
		submitting = true;
		try {
			const response = await api.createInspiration({
				generation_id: generation.id,
				filenames: selectedFilenames.length > 0 ? selectedFilenames : fileOptions.map((f) => f.filename),
				title: title.trim(),
				description: description.trim() || undefined
			});
			if (response.success) {
				toasts.success('Published to Inspirations');
				onClose();
			} else {
				toasts.error(response.error ?? response.message ?? 'Could not publish this generation.');
				settlementGate.reset();
			}
		} catch (e) {
			logger.error('Publish to Inspirations failed:', getErrorMessage(e));
			toasts.error('Could not publish this generation.');
			settlementGate.reset();
		} finally {
			submitting = false;
		}
	}
</script>

<!-- Opened from inside GenerationDetailsModal, which is itself portaled to
     <body> and forms its own stacking context — without portaling here too,
     this modal's z-index is scoped inside that context and can render behind
     the parent's own content. -->
<svelte:window on:keydown|capture={handleKeydown} />

<div use:portal>
<BaseModal isOpen={true} size="md" title="Publish to Inspirations" handleEscapeKey={false} on:close={handleCancel}>
	<div class="p-4 md:p-6 flex flex-col gap-4">
		<div>
			<label class="block text-xs font-medium text-fg mb-1" for="inspiration-title">Title</label>
			<input
				id="inspiration-title"
				type="text"
				class="input text-sm"
				placeholder="Give it a title…"
				maxlength={MAX_TITLE_LENGTH}
				bind:value={title}
				on:keydown={handleTitleKeydown}
			/>
		</div>

		<div>
			<label class="block text-xs font-medium text-fg mb-1" for="inspiration-description">
				Description
			</label>
			<textarea
				id="inspiration-description"
				class="input text-sm min-h-[5rem]"
				placeholder="Optional description…"
				bind:value={description}
			></textarea>
		</div>

		{#if fileOptions.length > 1}
			<div>
				<span class="block text-xs font-medium text-fg mb-1.5">Files to publish</span>
				<div class="space-y-1.5 max-h-40 overflow-y-auto">
					{#each fileOptions as file (file.filename)}
						<label class="flex items-center gap-2 text-xs text-fg-muted">
							<input
								type="checkbox"
								checked={!!selected[file.filename]}
								on:change={() => toggle(file.filename)}
							/>
							{file.label}
						</label>
					{/each}
				</div>
			</div>
		{/if}

		{#if error}
			<p class="text-xs text-danger">{error}</p>
		{/if}
	</div>
	<svelte:fragment slot="footer">
		<ConfirmFooter confirmLabel="Publish" busy={submitting} onCancel={handleCancel} onConfirm={handleConfirm} />
	</svelte:fragment>
</BaseModal>
</div>
