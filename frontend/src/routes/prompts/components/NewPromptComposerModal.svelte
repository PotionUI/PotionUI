<script lang="ts">
	import { api } from '$lib/services/api';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Input } from '$lib/components/ui';
	import type { Prompt } from '$lib/types/segments';
	import { toasts } from '$lib/stores/toast';
	import { splitPlainTextIntoSegments } from '$lib/utils/promptComposer';
	import PromptModelField from './PromptModelField.svelte';

	// Plain-text-first "New prompt": paste, optionally name it, Save. Structure
	// ("Split into segments") is opt-in — the default save is one content
	// segment, mirroring how a hand-typed prompt looked before this composer
	// existed (see the old PromptWorkspace.newPrompt()/savePrompt() pair).
	export let onClose: () => void;
	export let onCreated: (prompt: Prompt) => void;
	/** The workspace's active model filter, so a prompt created while filtering defaults to that model. */
	export let initialModelId: string | null = null;
	export let initialModelLabel: string | null = null;

	let content = '';
	let name = '';
	let modelId: string | null = initialModelId;
	let modelLabel: string | null = initialModelLabel;
	let modelPickerOpen = false;
	let splitIntoSegments = false;
	let saving = false;

	$: segmentPreview = splitIntoSegments ? splitPlainTextIntoSegments(content) : [];

	async function save() {
		const trimmed = content.trim();
		if (!trimmed || saving) return;
		saving = true;
		try {
			const segmentTexts = splitIntoSegments ? splitPlainTextIntoSegments(trimmed) : [trimmed];
			const response = await api.createPrompt({
				name: name.trim() || null,
				usage_hint: null,
				model_id: modelId,
				segments: segmentTexts.map((text) => ({
					type: 'content',
					content: text,
					chips: {},
					enabled: true
				}))
			});
			if (!response.success || !response.data) throw new Error(response.error || 'Save failed');
			toasts.success('Prompt saved');
			onCreated(response.data);
			onClose();
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to save prompt');
		} finally {
			saving = false;
		}
	}
</script>

<BaseModal
	isOpen={true}
	title="New prompt"
	sizeClass="md:max-w-lg md:w-full"
	handleEscapeKey={!modelPickerOpen}
	on:close={onClose}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="document" className="h-5 w-5 flex-shrink-0 text-fg-muted" />
	</svelte:fragment>

	<div class="space-y-4 p-6">
		<label>
			<span class="mb-1.5 block text-xs font-medium text-fg-muted">
				Name <span class="font-normal text-fg-subtle">(optional)</span>
			</span>
			<Input class="text-sm" bind:value={name} placeholder="Content preview is used when unnamed" />
		</label>

		<PromptModelField
			{modelId}
			{modelLabel}
			disabled={saving}
			bind:pickerOpen={modelPickerOpen}
			onChange={(model) => {
				modelId = model?.id ?? null;
				modelLabel = model?.label ?? null;
			}}
		/>

		<label>
			<span class="mb-1.5 block text-xs font-medium text-fg-muted">Content</span>
			<textarea
				class="input w-full resize-none py-2 text-sm"
				rows="8"
				placeholder="Paste anything — a whole prompt, notes, a description..."
				bind:value={content}
			></textarea>
		</label>

		<label class="flex cursor-pointer items-start gap-2.5 rounded border border-line-strong bg-surface-2 p-3">
			<input
				type="checkbox"
				class="mt-0.5 h-3.5 w-3.5 flex-shrink-0 accent-signal"
				bind:checked={splitIntoSegments}
			/>
			<span class="min-w-0">
				<span class="block text-xs font-medium text-fg">Split into segments</span>
				<span class="block text-2xs text-fg-subtle">
					Breaks the text on blank lines (or lines, if there are none) into separate segments instead of
					saving it as one block.
				</span>
				{#if splitIntoSegments && segmentPreview.length > 0}
					<span class="mt-1.5 block font-mono text-2xs tabular-nums text-fg-muted">
						{segmentPreview.length} segment{segmentPreview.length === 1 ? '' : 's'} detected
					</span>
				{/if}
			</span>
		</label>
	</div>

	<svelte:fragment slot="footer">
		<div class="flex justify-end gap-3 px-6 py-4">
			<Button variant="secondary" onclick={onClose} disabled={saving}>Cancel</Button>
			<Button variant="primary" onclick={save} loading={saving} disabled={!content.trim() || saving}>
				Save
			</Button>
		</div>
	</svelte:fragment>
</BaseModal>
