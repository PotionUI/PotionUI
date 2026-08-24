<script lang="ts">
	import { DetailPane } from '$lib/components/master-detail';
	import { phrasebookStore } from '$lib/stores/phrasebook';

	// Self-contained: reads/writes phrasebookStore directly. Extracted
	// verbatim from phrasebook/+page.svelte (category create/edit form).
	$: current = $phrasebookStore;
	$: form = current.categoryForm;
</script>

<DetailPane
	title={current.editMode === 'new-category' ? 'New Category' : 'Edit Category'}
	showDelete={current.editMode === 'category'}
	saveLabel={current.editMode === 'new-category' ? 'Create' : 'Save'}
	saveDisabled={!form.name || !form.path}
	on:save={() => phrasebookStore.handleSaveCategory()}
	on:cancel={() => phrasebookStore.handleCancelEdit()}
	on:delete={() => phrasebookStore.handleDeleteCategory()}
>
	<div class="space-y-4">
		<div>
			<label for="cat-name" class="label">Name *</label>
			<input
				id="cat-name"
				type="text"
				class="input"
				placeholder="Category name"
				value={form.name}
				on:input={(e) => phrasebookStore.setCategoryForm({ ...form, name: e.currentTarget.value })}
			/>
		</div>
		<div>
			<label for="cat-path" class="label">Path *</label>
			<input
				id="cat-path"
				type="text"
				class="input font-mono"
				placeholder="category.path"
				value={form.path}
				on:input={(e) => phrasebookStore.setCategoryForm({ ...form, path: e.currentTarget.value })}
			/>
			<p class="text-xs text-fg-subtle mt-1">Dot-separated path (e.g., emotions.positive)</p>
		</div>
		<div>
			<label for="cat-parent" class="label">Parent Category</label>
			<select
				id="cat-parent"
				class="input"
				value={form.parent_id}
				on:change={(e) => phrasebookStore.setCategoryForm({ ...form, parent_id: e.currentTarget.value || null })}
			>
				<option value={null}>None (Root)</option>
				{#each current.allCategories as cat}
					<option value={cat.id}>{cat.path}</option>
				{/each}
			</select>
		</div>
		<div>
			<label for="cat-desc" class="label">Description</label>
			<textarea
				id="cat-desc"
				class="input"
				rows="3"
				placeholder="Optional description"
				value={form.description}
				on:input={(e) => phrasebookStore.setCategoryForm({ ...form, description: e.currentTarget.value })}
			></textarea>
		</div>
	</div>
</DetailPane>
