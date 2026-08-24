<script lang="ts">
	import BaseModal from './BaseModal.svelte';
	import ModelAssignmentPicker from './ModelAssignmentPicker.svelte';

	export let assignedModelIds: string[] = [];
	export let title: string = 'Manage Model Assignments';
	export let subtitle: string = '';
	export let processingModelId: string | null = null;
	export let assignedUserId: string | undefined = undefined;
	export let assignedGroupId: string | undefined = undefined;
	export let onAssign: ((modelId: string) => void) | undefined = undefined;
	export let onUnassign: ((modelId: string) => void) | undefined = undefined;
	/** Reuse the admin model browser as a normal single-value picker. */
	export let selectionMode: 'assignments' | 'single' = 'assignments';
	export let selectedModelId: string | null = null;
	export let allowClear: boolean = false;
	export let onSelect: ((model: any) => void) | undefined = undefined;
	export let onClear: (() => void) | undefined = undefined;
	export let onClose: () => void;
	/** Only mounted from inside a parent `{#if}` today; default keeps that call site unchanged. */
	export let isOpen: boolean = true;
</script>

<BaseModal
	{isOpen}
	{title}
	{subtitle}
	sizeClass="md:max-w-6xl md:w-full"
	on:close={onClose}
>
	<div class="h-full flex flex-col">
		<ModelAssignmentPicker
			{assignedModelIds}
			{processingModelId}
			{assignedUserId}
			{assignedGroupId}
			{onAssign}
			{onUnassign}
			{selectionMode}
			{selectedModelId}
			{allowClear}
			{onSelect}
			{onClear}
		/>
	</div>
</BaseModal>
