<script lang="ts">
	import { api } from '$lib/services/api';
	import type { PromptImportFileOutcome, PromptImportResult } from '$lib/services/api/prompts';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Alert, Badge, Button, IconButton, Input, Spinner } from '$lib/components/ui';
	import { toasts } from '$lib/stores/toast';
	import { formatBytes } from '$lib/utils/format';
	import {
		IMPORT_ACCEPT,
		IMPORT_FORMAT_OPTIONS,
		buildPromptImportFormData,
		hasPromptImportInput,
		importFormatLabel,
		importSkipReasonCopy,
		type PromptImportFormatValue
	} from './promptImport';

	export let onClose: () => void;
	export let onImported: () => void;

	let files: File[] = [];
	let pastedText = '';
	let format: PromptImportFormatValue = '';
	let modelName = '';
	let baseModel = '';
	let dragging = false;
	let submitting = false;
	let result: PromptImportResult | null = null;
	let failureFiles: PromptImportFileOutcome[] = [];
	let failureMessage = '';

	let fileInput: HTMLInputElement;

	$: selectedFormat = IMPORT_FORMAT_OPTIONS.find((option) => option.value === format) ?? IMPORT_FORMAT_OPTIONS[0];
	$: canSubmit = hasPromptImportInput({ files, pastedText }) && !submitting;

	function addFiles(list: FileList | File[]) {
		const incoming = Array.from(list);
		if (incoming.length === 0) return;
		files = [...files, ...incoming];
	}

	function removeFile(index: number) {
		files = files.filter((_, i) => i !== index);
	}

	function onFileInputChange(e: Event) {
		const input = e.target as HTMLInputElement;
		if (input.files) addFiles(input.files);
		input.value = '';
	}

	function onDrop(e: DragEvent) {
		e.preventDefault();
		dragging = false;
		if (e.dataTransfer?.files) addFiles(e.dataTransfer.files);
	}

	function onDragOver(e: DragEvent) {
		e.preventDefault();
		dragging = true;
	}

	function onDragLeave() {
		dragging = false;
	}

	function resetForm() {
		files = [];
		pastedText = '';
		format = '';
		modelName = '';
		baseModel = '';
		result = null;
		failureFiles = [];
		failureMessage = '';
	}

	async function submit() {
		if (!canSubmit) return;
		submitting = true;
		failureFiles = [];
		failureMessage = '';
		try {
			const formData = buildPromptImportFormData({ files, pastedText, format, modelName, baseModel });
			const response = await api.importPrompts(formData);
			if (response.success && response.data) {
				result = response.data;
			} else if (response.error === 'nothing_imported') {
				failureMessage = response.message || 'Nothing could be imported from what was provided.';
				failureFiles = response.data?.files || [];
			} else {
				throw new Error(response.error || response.message || 'Import failed');
			}
		} catch (error) {
			toasts.error(error instanceof Error ? error.message : 'Failed to import prompts');
		} finally {
			submitting = false;
		}
	}

	function done() {
		if (result) onImported();
		onClose();
	}

	function importMore() {
		resetForm();
	}
</script>

<BaseModal isOpen={true} title="Import prompts" sizeClass="md:max-w-2xl md:w-full" on:close={onClose}>
	<svelte:fragment slot="headerIcon">
		<Icon name="upload" className="h-5 w-5 flex-shrink-0 text-fg-muted" />
	</svelte:fragment>

	{#if result}
		<div class="space-y-4 p-6">
			<Alert variant="success" icon="check">
				Imported {result.imported} prompt{result.imported === 1 ? '' : 's'}
				{#if result.skipped > 0}, skipped {result.skipped}{/if}
				 out of {result.total} found.
			</Alert>

			{#if result.files.length > 0}
				<div class="overflow-x-auto rounded border border-line-strong">
					<table class="w-full text-left text-xs">
						<thead class="bg-surface-2 text-fg-muted">
							<tr>
								<th class="px-3 py-2 font-medium">File</th>
								<th class="px-3 py-2 font-medium">Format</th>
								<th class="px-3 py-2 font-medium text-right">Imported</th>
								<th class="px-3 py-2 font-medium text-right">Skipped</th>
							</tr>
						</thead>
						<tbody class="divide-y divide-line">
							{#each result.files as fileResult}
								{@const reasonCopy = importSkipReasonCopy(fileResult.reason)}
								<tr>
									<td class="max-w-[200px] truncate px-3 py-2 text-fg" title={fileResult.filename}>
										{fileResult.filename}
									</td>
									<td class="px-3 py-2">
										<Badge size="sm">{importFormatLabel(fileResult.format)}</Badge>
									</td>
									<td class="px-3 py-2 text-right font-mono tabular-nums text-fg">
										{fileResult.imported}
									</td>
									<td class="px-3 py-2 text-right">
										<span class="font-mono tabular-nums text-fg">{fileResult.skipped}</span>
										{#if fileResult.skipped > 0 && reasonCopy}
											<span class="ml-1.5 text-2xs text-fg-subtle">({reasonCopy})</span>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</div>
	{:else}
		{#if submitting}
			<div class="flex items-center gap-2 border-b border-line-strong bg-surface-2 px-6 py-2.5 text-xs text-fg-muted">
				<Spinner size="sm" />
				Importing...
			</div>
		{/if}
		<fieldset class="min-w-0 space-y-4 border-0 m-0 p-6" disabled={submitting}>
			{#if failureMessage}
				<Alert variant="danger" icon="warning">
					{failureMessage}
					{#if failureFiles.length > 0}
						<ul class="mt-2 space-y-1 text-xs">
							{#each failureFiles as fileResult}
								{@const reasonCopy = importSkipReasonCopy(fileResult.reason)}
								<li class="flex items-center justify-between gap-2">
									<span class="truncate">{fileResult.filename}</span>
									{#if reasonCopy}<span class="flex-shrink-0 text-fg-subtle">{reasonCopy}</span>{/if}
								</li>
							{/each}
						</ul>
					{/if}
				</Alert>
			{/if}

			<div>
				<span class="mb-1.5 block text-xs font-medium text-fg-muted">Files</span>
				<button
					type="button"
					class="flex w-full flex-col items-center gap-2 rounded border-2 border-dashed px-4 py-6 text-center transition-colors disabled:cursor-not-allowed disabled:opacity-50 {dragging
						? 'border-signal bg-signal/5'
						: 'border-line-strong hover:border-line-hover'}"
					ondrop={onDrop}
					ondragover={onDragOver}
					ondragleave={onDragLeave}
					onclick={() => fileInput?.click()}
				>
					<Icon name="upload" className="h-5 w-5 text-fg-subtle" />
					<span class="text-xs text-fg-muted">
						Drop files here, or <span class="text-fg underline">browse</span>
					</span>
					<span class="text-2xs text-fg-subtle">CSV, JSON, YAML, TXT, PNG, JPEG, WebP</span>
				</button>
				<input
					bind:this={fileInput}
					type="file"
					multiple
					accept={IMPORT_ACCEPT}
					class="hidden"
					onchange={onFileInputChange}
				/>

				{#if files.length > 0}
					<ul class="mt-2 space-y-1">
						{#each files as file, index}
							<li
								class="flex items-center justify-between gap-2 rounded border border-line-strong bg-surface-2 px-2.5 py-1.5"
							>
								<span class="min-w-0 truncate text-xs text-fg" title={file.name}>{file.name}</span>
								<div class="flex flex-shrink-0 items-center gap-2">
									<span class="font-mono text-2xs tabular-nums text-fg-subtle">{formatBytes(file.size)}</span>
									<Tooltip text="Remove file" position="top">
										<IconButton icon="close" label="Remove {file.name}" size="sm" onclick={() => removeFile(index)} />
									</Tooltip>
								</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<label>
				<span class="mb-1.5 block text-xs font-medium text-fg-muted">Or paste text</span>
				<textarea
					class="input w-full resize-none py-2 text-sm"
					rows="4"
					placeholder="Paste a styles.csv, a wildcard file, or one prompt per line..."
					bind:value={pastedText}
				></textarea>
			</label>

			<label>
				<span class="mb-1.5 block text-xs font-medium text-fg-muted">Format</span>
				<select class="input text-sm" bind:value={format}>
					{#each IMPORT_FORMAT_OPTIONS as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
				<span class="mt-1 block text-2xs text-fg-subtle">{selectedFormat.hint}</span>
			</label>

			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				<label>
					<span class="mb-1.5 block text-xs font-medium text-fg-muted">
						Model name <span class="font-normal text-fg-subtle">(optional)</span>
					</span>
					<Input class="text-sm" bind:value={modelName} placeholder="Fills gaps only" />
				</label>
				<label>
					<span class="mb-1.5 block text-xs font-medium text-fg-muted">
						Base model <span class="font-normal text-fg-subtle">(optional)</span>
					</span>
					<Input class="text-sm" bind:value={baseModel} placeholder="Fills gaps only" />
				</label>
			</div>
			<span class="-mt-2 block text-2xs text-fg-subtle">
				Only applied to imported prompts that don't already carry a model or base model.
			</span>
		</fieldset>
	{/if}

	<svelte:fragment slot="footer">
		<div class="flex items-center justify-end gap-3 px-6 py-4">
			{#if result}
				<Button variant="secondary" onclick={importMore}>Import more</Button>
				<Button variant="primary" onclick={done}>Done</Button>
			{:else}
				<Button variant="secondary" onclick={onClose} disabled={submitting}>Cancel</Button>
				<Button variant="primary" onclick={submit} loading={submitting} disabled={!canSubmit}>
					Import
				</Button>
			{/if}
		</div>
	</svelte:fragment>
</BaseModal>
