<script lang="ts">
	let {
		isOpen = false,
		multiple = false,
		title = undefined,
		initialSource = 'history',
		mediaTypes = [],
		maxCount = null,
		kits = undefined,
		onPick = () => {},
		onConfirm = () => {},
		onClose = () => {}
	}: {
		isOpen?: boolean;
		multiple?: boolean;
		title?: string;
		initialSource?: string;
		mediaTypes?: string[];
		maxCount?: number | null;
		kits?: unknown;
		onPick?: (picked: unknown) => void;
		onConfirm?: (keys: string[], items: unknown[]) => void;
		onClose?: () => void;
	} = $props();

	const history = {
		key: 'gen-new:91',
		mediaType: 'image',
		filename: 'new.png',
		origin: { kind: 'history', generationId: 'gen-new', fileId: 91 },
		generation: { id: 'gen-new', prompt: 'a new one', form_data: {} },
		file: { id: 91, file_path: 'generations/gen-new/new.png', file_type: 'image', width: 640, height: 480, file_size: 1000 }
	};
	const library = {
		key: 'lib-new',
		mediaType: 'image',
		filename: 'lib-new.png',
		origin: { kind: 'library', itemId: 'lib-new' },
		item: {
			id: 'lib-new',
			filename: 'lib-new.png',
			original_filename: 'lib-new.png',
			url: '/api/media/uploads/lib-new.png',
			media_type: 'image',
			width: 320,
			height: 200,
			size: 500
		}
	};
	const kit = {
		key: 'kit:ki9',
		mediaType: 'image',
		filename: 'kit-face',
		origin: { kind: 'kit', kitId: 'kt_1', itemId: 'ki9' },
		kit: { id: 'kt_1', name: 'Mara', handle: 'mara' },
		kitItem: {
			id: 'ki9',
			kit_id: 'kt_1',
			kind: 'image',
			name: 'kit-face',
			media_ref: 'up_9',
			url: '/api/media/uploads/kit-face.png',
			thumbnail_url: null,
			width: 512,
			height: 512,
			duration_seconds: null,
			text: null,
			missing: false,
			position: 0,
			created_at: ''
		}
	};
	const bySource: Record<string, unknown> = { history, library, kit };
</script>

{#if isOpen}
	<div
		data-stub="picker"
		data-multiple={String(multiple)}
		data-title={title ?? ''}
		data-source={initialSource}
		data-media-types={mediaTypes.join(',')}
		data-max-count={maxCount === null ? '' : String(maxCount)}
		data-has-kits={kits ? 'true' : 'false'}
	>
		{#each ['history', 'library', 'kit'] as name (name)}
			<button type="button" data-stub-pick={name} onclick={() => onPick(bySource[name])}>pick {name}</button>
		{/each}
		<button
			type="button"
			data-stub-confirm
			onclick={() => onConfirm(['gen-new:91', 'lib-new', 'kit:ki9'], [history, library, kit])}
		>
			confirm
		</button>
		<button type="button" data-stub-close onclick={() => onClose()}>close</button>
	</div>
{/if}
