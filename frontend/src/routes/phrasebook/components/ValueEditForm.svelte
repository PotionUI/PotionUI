<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { DetailPane } from '$lib/components/master-detail';
	import AIAssistButton from '$lib/components/AIAssistButton.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { phrasebookStore, selectedValue } from '$lib/stores/phrasebook';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (value create/edit form).
	$: current = $phrasebookStore;
	$: form = current.valueForm;
</script>

<DetailPane
	title={current.editMode === 'new-value' ? 'New Value' : 'Edit Value'}
	showDelete={current.editMode === 'value'}
	saveLabel={current.editMode === 'new-value' ? 'Create' : 'Save'}
	saveDisabled={!form.label || !form.value}
	on:save={() => phrasebookStore.handleSaveValue()}
	on:cancel={() => phrasebookStore.handleCancelEdit()}
	on:delete={() => phrasebookStore.handleDeleteValue()}
>
	<div class="space-y-4">
		<!-- Preview image (only when editing existing value) -->
		{#if current.editMode === 'value'}
			{#if $selectedValue?.preview_file_id}
				<div class="flex justify-center p-4 bg-surface-2 rounded-xl border border-line">
					<img
						src={api.getFileURL($selectedValue.preview_file_id, 'large')}
						alt={$selectedValue.label}
						class="max-w-full max-h-72 rounded-lg object-contain shadow-raised"
					/>
				</div>
			{:else}
				<div
					class="flex flex-col items-center justify-center gap-2.5 py-10 px-4 rounded-xl border border-dashed border-line text-center"
					style={$selectedValue?.label ? placeholderTint($selectedValue.label) : 'background: rgb(var(--surface-2));'}
				>
					<div class="bg-surface-3 rounded-full p-2.5 shadow-raised">
						<Icon name="image" className="w-5 h-5 text-fg-subtle" />
					</div>
					<p class="text-sm text-fg-muted">No example image</p>
					<p class="text-xs text-fg-subtle">Generate examples from the category panel</p>
				</div>
			{/if}
		{/if}
		<div>
			<label for="val-label" class="label">Label *</label>
			<input
				id="val-label"
				type="text"
				class="input"
				placeholder="Display label"
				value={form.label}
				on:input={(e) => phrasebookStore.setValueForm({ ...form, label: e.currentTarget.value })}
			/>
		</div>
		<div>
			<div class="flex items-center justify-between mb-1.5">
				<label for="val-value" class="label mb-0">Value *</label>
				<AIAssistButton
					currentContent={form.value}
				/>
			</div>
			<textarea
				id="val-value"
				class="input font-mono text-sm"
				rows="4"
				placeholder="Actual value"
				value={form.value}
				on:input={(e) => phrasebookStore.setValueForm({ ...form, value: e.currentTarget.value })}
			></textarea>
		</div>
		<div>
			<label for="val-category" class="label">Category</label>
			<select
				id="val-category"
				class="input"
				value={form.category_id}
				on:change={(e) => phrasebookStore.setValueForm({ ...form, category_id: e.currentTarget.value })}
			>
				{#each current.allCategories as cat}
					<option value={cat.id}>{cat.path}</option>
				{/each}
			</select>
		</div>
		<div>
			<label for="val-order" class="label">Sort Order</label>
			<input
				id="val-order"
				type="number"
				class="input tabular-nums"
				value={form.sort_order}
				on:input={(e) => phrasebookStore.setValueForm({ ...form, sort_order: parseInt(e.currentTarget.value, 10) || 0 })}
			/>
		</div>
	</div>
</DetailPane>
