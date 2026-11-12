<script lang="ts">
	import MediaPickerModal from './mediaPicker/MediaPickerModal.svelte';
	import type { GenerationFile, GenerationHistoryItem } from '$lib/types/history';
	import type { PickedMedia, PickerMediaType } from './mediaPickerSelection';

	let {
		isOpen = false,
		onClose,
		onSelect = () => {},
		mediaType = undefined,
		title = 'Select Media from Generation History',
		multiple = false,
		selectedKeys = undefined,
		onSelectionChange = undefined,
		onConfirm = undefined,
		selectableMediaTypes = undefined
	}: {
		isOpen?: boolean;
		onClose: () => void;
		onSelect?: (generation: GenerationHistoryItem, file: GenerationFile) => void;
		mediaType?: 'image' | 'video' | 'audio' | 'mesh';
		title?: string;
		multiple?: boolean;
		selectedKeys?: string[];
		onSelectionChange?: (keys: string[], items: PickedMedia[]) => void;
		onConfirm?: (keys: string[], items: PickedMedia[]) => void;
		selectableMediaTypes?: PickerMediaType[];
	} = $props();

	function handlePick(picked: PickedMedia) {
		if (picked.generation && picked.file) onSelect(picked.generation, picked.file);
	}
</script>

<MediaPickerModal
	{isOpen}
	{onClose}
	{title}
	sources={['history']}
	{mediaType}
	{multiple}
	{selectedKeys}
	{onSelectionChange}
	{onConfirm}
	{selectableMediaTypes}
	closeOnPick={false}
	onPick={handlePick}
/>
