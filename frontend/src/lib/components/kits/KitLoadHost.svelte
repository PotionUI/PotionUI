<script lang="ts">
	import { clearKitLoadRequest, kitLoadRequest } from '$lib/stores/kitLoadRequests';
	import { addKitRow, snapshotOf, splitKitRows, withKitRows } from '$lib/kits/kitMarker';
	import { defaultKitField, kitFieldsOf, type PromptSpecLike } from '$lib/kits/promptKits';
	import { mediaCountOf } from '$lib/kits/kitCounts';
	import { kitDirectory } from '$lib/stores/kits';
	import type { KitDetail, KitRow } from '$lib/kits/types';
	import MediaPickerModal from '$lib/components/modals/mediaPicker/MediaPickerModal.svelte';

	let {
		formData,
		specs,
		modelName = null,
		onChange
	}: {
		formData: Record<string, unknown> | null | undefined;
		specs: readonly PromptSpecLike[];
		modelName?: string | null;
		onChange: (field: string, value: unknown[]) => void;
	} = $props();

	let fields = $derived(kitFieldsOf(specs, formData));
	let target = $derived.by(() => {
		const request = $kitLoadRequest;
		if (!request) return null;
		const wanted = request.field ? fields.find((entry) => entry.field === request.field) : null;
		return wanted ?? fields.find((entry) => entry.field === defaultKitField(fields)) ?? null;
	});
	let held = $derived(
		target
			? target.rows.reduce(
					(sum, row) => sum + ($kitDirectory.get(row.kit) ? mediaCountOf($kitDirectory.get(row.kit)!.items, target.accepted) : 0),
					0
				)
			: 0
	);

	function loaded(kits: KitDetail[]) {
		if (!target) return;
		const value = formData?.[target.field];
		const media = splitKitRows(Array.isArray(value) ? (value as unknown[]) : []).media;
		const rows = kits.reduce<KitRow[]>((all, kit) => addKitRow(all, { kit: kit.id, snapshot: snapshotOf(kit) }), target.rows);
		onChange(target.field, withKitRows(media, rows));
	}
</script>

{#if $kitLoadRequest && target}
	<MediaPickerModal
		isOpen={true}
		onClose={clearKitLoadRequest}
		title="Load a kit"
		sources={['kits']}
		mediaTypes={['image', 'video', 'audio']}
		multiple
		kitItems={false}
		kits={{ accepted: target.accepted, loadedIds: target.rows.map((row) => row.kit), onLoad: loaded }}
		confirmLabel={({ count, kitNames }) => (count === 0 ? 'Load' : kitNames.length === 1 ? `Load ${kitNames[0]}` : `Load ${kitNames.length} kits`)}
	/>
{/if}
