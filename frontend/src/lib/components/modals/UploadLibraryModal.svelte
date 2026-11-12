<script lang="ts">
	import MediaPickerModal from './mediaPicker/MediaPickerModal.svelte';
	import type { LibraryItem } from '$lib/services/api/library';
	import type { PickedMedia, PickerMediaType } from './mediaPickerSelection';

	let {
		isOpen = false,
		onClose,
		onSelect = () => {},
		mediaType = undefined,
		title = 'Select from Your Library',
		multiple = false,
		selectedKeys = undefined,
		onSelectionChange = undefined,
		onConfirm = undefined,
		selectableMediaTypes = undefined
	}: {
		isOpen?: boolean;
		onClose: () => void;
		onSelect?: (item: LibraryItem) => void;
		mediaType?: 'image' | 'video' | 'audio';
		title?: string;
		multiple?: boolean;
		selectedKeys?: string[];
		onSelectionChange?: (keys: string[], items: PickedMedia[]) => void;
		onConfirm?: (keys: string[], items: PickedMedia[]) => void;
		selectableMediaTypes?: PickerMediaType[];
	} = $props();

	function handlePick(picked: PickedMedia) {
		if (picked.item) onSelect(picked.item);
	}
</script>

<MediaPickerModal
	{isOpen}
	{onClose}
	{title}
	sources={['library']}
	{mediaType}
	{multiple}
	{selectedKeys}
	{onSelectionChange}
	{onConfirm}
	{selectableMediaTypes}
	closeOnPick={false}
	onPick={handlePick}
/>
