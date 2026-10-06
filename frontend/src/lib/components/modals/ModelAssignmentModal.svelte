<script lang="ts">
	import BaseModal from './BaseModal.svelte';
	import ModelAssignmentPicker from './ModelAssignmentPicker.svelte';

	export let title: string = 'Select model';
	export let subtitle: string = '';
	export let selectionMode: 'single' | 'multi' = 'single';
	export let selectedModelId: string | null = null;
	export let allowClear: boolean = false;
	export let onSelect: ((model: any) => void) | undefined = undefined;
	export let onClear: (() => void) | undefined = undefined;
	export let initialType: string = 'all';
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
			{selectionMode}
			{selectedModelId}
			{allowClear}
			selectedType={initialType}
			{onSelect}
			{onClear}
		/>
	</div>
</BaseModal>
