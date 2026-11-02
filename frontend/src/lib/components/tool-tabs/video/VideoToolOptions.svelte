<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Kbd } from '$lib/components/ui';
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { CROP_ASPECTS, mirrorCropRect, rotateCropRect, turn } from '$lib/media/editors/cropGeometry';
	import {
		FIX_PARAMS,
		GRAIN_AMOUNT_PARAMS,
		GRAIN_PARAMS,
		REPLACE_PARAMS,
		SPEED_PARAMS,
		resizeParams,
		volumeParams
	} from '$lib/tool-tabs/video/params';
	import { formatEditorTime, parseEditorTime } from '$lib/tool-tabs/video/videoState';
	import {
		fixed2,
		readCrop,
		readTrim,
		type ClipInfo,
		type RailToolId,
		type VideoOp,
		type VideoParams
	} from '$lib/tool-tabs/video/videoSteps';

	type EffectId = 'film_grain' | 'fix_colors';

	let {
		tool,
		op,
		params,
		effect = 'film_grain',
		clip,
		playhead,
		outputLength,
		aspect = 'free',
		sizeOptions = [],
		phone = false,
		hasAudio = true,
		previewState = 'idle',
		busy = null,
		onparams,
		onaspect,
		oneffect,
		onsetin,
		onsetout,
		onpreview,
		oncancelpreview,
		onframe,
		onpickaudio
	}: {
		tool: RailToolId;
		op: VideoOp | null;
		params: VideoParams;
		effect?: EffectId;
		clip: ClipInfo;
		playhead: number;
		outputLength: number;
		aspect?: string;
		sizeOptions?: { label: string; short: number }[];
		phone?: boolean;
		hasAudio?: boolean;
		previewState?: 'idle' | 'rendering' | 'ready';
		busy?: string | null;
		onparams: (op: VideoOp, params: VideoParams) => void;
		onaspect: (key: string) => void;
		oneffect: (effect: EffectId) => void;
		onsetin: () => void;
		onsetout: () => void;
		onpreview: () => void;
		oncancelpreview: () => void;
		onframe: (action: 'save' | 'open') => void;
		onpickaudio: (from: 'library' | 'upload') => void;
	} = $props();

	let editing = $state<{ which: 'in' | 'out'; text: string } | null>(null);

	let trim = $derived(op === 'trim' ? readTrim(params) : { start: 0, end: clip.duration });
	let crop = $derived(op === 'crop' ? readCrop(params) : null);

	function set(name: string, value: unknown) {
		if (!op) return;
		onparams(op, { ...params, [name]: value });
	}

	function trimText(which: 'in' | 'out'): string {
		if (editing?.which === which) return editing.text;
		return formatEditorTime(which === 'in' ? trim.start : trim.end);
	}

	function commitTrim(which: 'in' | 'out') {
		const text = editing?.which === which ? editing.text : null;
		editing = null;
		if (text === null) return;
		const value = parseEditorTime(text);
		if (value === null) return;
		const next = Math.max(0, Math.min(clip.duration, value));
		onparams('trim', which === 'in' ? { ...params, start: Math.min(next, trim.end - 0.05) } : { ...params, end: Math.max(next, trim.start + 0.05) });
	}

	function trimKey(event: KeyboardEvent, which: 'in' | 'out') {
		if (event.key === 'Enter') {
			event.preventDefault();
			event.stopPropagation();
			commitTrim(which);
			(event.currentTarget as HTMLInputElement).blur();
		} else if (event.key === 'Escape') {
			editing = null;
			(event.currentTarget as HTMLInputElement).blur();
		}
	}

	function rotate(quarters: number) {
		if (!crop) return;
		onparams('crop', { ...params, rect: rotateCropRect(crop.rect, quarters), rotation: turn(crop.rotation, quarters) });
	}

	function flip(axis: 'horizontal' | 'vertical') {
		if (!crop) return;
		const key = axis === 'horizontal' ? 'flipH' : 'flipV';
		onparams('crop', { ...params, rect: mirrorCropRect(crop.rect, axis), [key]: !crop[key] });
	}

	const sectionLabel = 'font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle';
	const readoutRow = 'flex items-center justify-between text-sm';
</script>

{#snippet trimFields()}
	<div class="grid grid-cols-2 gap-2">
		{#each ['in', 'out'] as const as which (which)}
			<label class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 focus-within:border-signal">
				<input
					class="min-w-0 flex-1 bg-transparent font-mono text-base tabular-nums text-fg outline-none"
					value={trimText(which)}
					aria-label={which === 'in' ? 'In point' : 'Out point'}
					inputmode="decimal"
					onfocus={() => (editing = { which, text: formatEditorTime(which === 'in' ? trim.start : trim.end) })}
					oninput={(event) => (editing = { which, text: (event.currentTarget as HTMLInputElement).value })}
					onblur={() => commitTrim(which)}
					onkeydown={(event) => trimKey(event, which)}
					data-trim-field={which}
				/>
				<span class="font-mono text-sm uppercase text-fg-subtle">{which}</span>
			</label>
		{/each}
	</div>
{/snippet}

{#snippet previewButton()}
	{#if previewState === 'rendering'}
		<Button variant="secondary" size="sm" class="h-8 w-full" icon="loading" onclick={oncancelpreview} attrs={{ 'data-preview-clip': 'rendering' }}>
			Rendering preview · Cancel
		</Button>
	{:else}
		<Button variant="secondary" size="sm" class="h-8 w-full" icon="play" onclick={onpreview} attrs={{ 'data-preview-clip': previewState }}>
			Preview 3 s <span class="font-mono text-sm text-fg-subtle">480p</span>
		</Button>
	{/if}
{/snippet}

{#if tool === 'trim'}
	{#if phone}
		{@render trimFields()}
	{:else}
		<div class="flex flex-col gap-2">
			<span class={sectionLabel}>In and out</span>
			{@render trimFields()}
			<div class="grid grid-cols-2 gap-2">
				<Button variant="secondary" size="sm" class="h-8" onclick={onsetin} attrs={{ 'data-trim-set': 'in' }}>Set in <Kbd keys="I" /></Button>
				<Button variant="secondary" size="sm" class="h-8" onclick={onsetout} attrs={{ 'data-trim-set': 'out' }}>Set out <Kbd keys="O" /></Button>
			</div>
		</div>
		<div class="flex flex-col gap-1.5 border-b border-line pb-4">
			<div class={readoutRow}><span class="text-fg-muted">Source</span><span class="font-mono tabular-nums text-fg">{fixed2(clip.duration)} s</span></div>
			<div class={readoutRow}><span class="text-fg-muted">Output</span><span class="font-mono tabular-nums text-success" data-trim-output>{fixed2(outputLength)} s</span></div>
		</div>
		<p class="text-sm text-fg-muted">Drag the white handles on the scrub bar, or set them at the playhead.</p>
	{/if}
{:else if tool === 'split'}
	<div class="flex flex-col gap-2">
		<span class={sectionLabel}>Split at</span>
		<div class="flex items-center gap-2">
			<span class="flex h-9 flex-1 items-center rounded border border-line-strong bg-surface-2 px-2.5 font-mono text-base tabular-nums text-fg" data-split-at>{formatEditorTime(Number(params.at ?? 0))}</span>
			<Button variant="secondary" size="sm" class="h-9" onclick={() => onparams('split', { at: playhead })} attrs={{ 'data-split-playhead': '' }}>Use playhead</Button>
		</div>
	</div>
	<p class="text-sm text-fg-muted">Saving makes two Library items: up to this point, and from it.</p>
{:else if tool === 'crop' && crop}
	<div class="flex flex-col gap-2">
		<span class={sectionLabel}>Aspect</span>
		<div class="grid grid-cols-3 gap-1.5" role="group" aria-label="Aspect">
			{#each CROP_ASPECTS as entry (entry.key)}
				<Button variant="secondary" size="sm" class="h-8 font-mono" pressed={aspect === entry.key} onclick={() => onaspect(entry.key)}>{entry.label}</Button>
			{/each}
		</div>
	</div>
	<div class="flex flex-col gap-2">
		<span class={sectionLabel}>Rotate and flip</span>
		<div class="grid grid-cols-4 gap-1.5">
			<Button variant="secondary" size="sm" class="h-8" icon="editor-rotate-left" ariaLabel="Rotate left" onclick={() => rotate(-1)} />
			<Button variant="secondary" size="sm" class="h-8" icon="editor-rotate-right" ariaLabel="Rotate right" onclick={() => rotate(1)} />
			<Button variant="secondary" size="sm" class="h-8" icon="flip" ariaLabel="Flip horizontally" pressed={crop.flipH} onclick={() => flip('horizontal')} />
			<Button variant="secondary" size="sm" class="h-8" icon="editor-flip-v" ariaLabel="Flip vertically" pressed={crop.flipV} onclick={() => flip('vertical')} />
		</div>
	</div>
	<p class="text-sm text-fg-muted">Drag the corners on the picture. The playback shows the crop live.</p>
{:else if tool === 'resize' && op}
	<ToolForm params={resizeParams(sizeOptions)} values={params} onChange={(name, value) => set(name, name === 'short' ? Number(value) : value)} />
{:else if tool === 'speed' && op}
	<ToolForm params={SPEED_PARAMS} values={params} onChange={set} />
	<div class={readoutRow}><span class="text-fg-muted">Output</span><span class="font-mono tabular-nums text-fg" data-speed-output>{fixed2(outputLength)} s</span></div>
{:else if tool === 'mute'}
	<p class="text-sm text-fg-muted">{hasAudio ? 'Removes the sound from the saved clip. Playback goes quiet so you can hear it.' : 'This clip has no sound to remove.'}</p>
{:else if tool === 'volume' && op}
	<ToolForm params={volumeParams(outputLength || clip.duration)} values={params} onChange={set} />
{:else if tool === 'replace' && op}
	<div class="flex flex-col gap-2">
		<span class={sectionLabel}>Track</span>
		<div class="flex h-9 items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 text-sm" data-replace-track>
			<Icon name="editor-music" className="h-3.5 w-3.5 flex-none text-fg-muted" />
			<span class="min-w-0 flex-1 truncate {params.audio ? 'text-fg' : 'text-fg-subtle'}">{params.audio ? String(params.name || 'Audio') : 'No track picked'}</span>
		</div>
		<div class="grid grid-cols-2 gap-2">
			<Button variant="secondary" size="sm" class="h-8" icon="library" disabled={busy === 'audio'} onclick={() => onpickaudio('library')} attrs={{ 'data-replace-pick': 'library' }}>Library</Button>
			<Button variant="secondary" size="sm" class="h-8" icon="upload" loading={busy === 'audio'} onclick={() => onpickaudio('upload')} attrs={{ 'data-replace-pick': 'upload' }}>Upload</Button>
		</div>
	</div>
	<ToolForm params={REPLACE_PARAMS} values={params} onChange={set} />
{:else if tool === 'effects'}
	{#if phone}
		{#if effect === 'film_grain'}
			<ToolForm params={GRAIN_AMOUNT_PARAMS} values={params} onChange={(name, value) => onparams('film_grain', { ...params, [name]: value })} />
		{:else}
			<ToolForm params={FIX_PARAMS.slice(2)} values={params} onChange={(name, value) => onparams('fix_colors', { ...params, [name]: value })} />
		{/if}
		<div class="grid grid-cols-2 gap-2">
			{@render previewButton()}
			<Button variant="secondary" size="sm" class="h-8" icon={effect === 'film_grain' ? 'wand' : 'editor-effects'} onclick={() => oneffect(effect === 'film_grain' ? 'fix_colors' : 'film_grain')}>
				{effect === 'film_grain' ? 'Fix colours' : 'Film grain'}
			</Button>
		</div>
	{:else}
		{#each [{ id: 'film_grain', label: 'Film grain', icon: 'editor-effects' }, { id: 'fix_colors', label: 'Fix colours', icon: 'wand' }] as const as card (card.id)}
			{@const open = effect === card.id}
			<div class="rounded-lg border {open ? 'border-signal/50 bg-surface-2' : 'border-line bg-surface-1'}" data-effect-card={card.id} data-open={open || undefined}>
				<button
					type="button"
					class="flex h-10 w-full items-center gap-2 px-3 text-left text-base text-fg"
					aria-expanded={open}
					onclick={() => oneffect(card.id)}
				>
					<Icon name={card.icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
					<span class="min-w-0 flex-1 truncate">{card.label}</span>
					{#if open}
						<Badge variant="signal" size="sm">Preview</Badge>
					{:else}
						<Icon name="plus" className="h-3.5 w-3.5 text-fg-muted" />
					{/if}
				</button>
				{#if open}
					<div class="flex flex-col gap-3 px-3 pb-3">
						{#if card.id === 'film_grain'}
							<ToolForm params={GRAIN_PARAMS} values={params} onChange={(name, value) => onparams('film_grain', { ...params, [name]: name === 'size' ? Number(value) : value })} />
							<span class="self-end font-mono text-sm uppercase tabular-nums text-fg-subtle" data-grain-seed>Seed {Number(params.seed ?? 0)}</span>
						{:else}
							<ToolForm params={FIX_PARAMS} values={params} onChange={(name, value) => onparams('fix_colors', { ...params, [name]: value })} />
						{/if}
					</div>
				{/if}
			</div>
		{/each}
		<div class="flex h-10 items-center gap-2 rounded-lg border border-line px-3 text-base text-fg-disabled" aria-disabled="true" data-effect-later>
			<Icon name="palette" className="h-3.5 w-3.5 flex-none" />
			<span class="flex-1">Looks</span>
			<span class="font-mono text-sm uppercase tracking-[0.08em]">Later</span>
		</div>
		{@render previewButton()}
	{/if}
{:else if tool === 'frame'}
	<div class="flex flex-col gap-2">
		<span class={sectionLabel}>Frame at</span>
		<span class="flex h-9 items-center rounded border border-line-strong bg-surface-2 px-2.5 font-mono text-base tabular-nums text-fg" data-frame-at>{formatEditorTime(playhead)}</span>
	</div>
	<div class="flex flex-col gap-2">
		<Button variant="secondary" size="sm" class="h-8" icon="image" loading={busy === 'frame-save'} disabled={!!busy} onclick={() => onframe('save')} attrs={{ 'data-frame-action': 'save' }}>Save frame as image</Button>
		<Button variant="secondary" size="sm" class="h-8" icon="sliders-horizontal" loading={busy === 'frame-open'} disabled={!!busy} onclick={() => onframe('open')} attrs={{ 'data-frame-action': 'open' }}>Open frame in Image editor</Button>
	</div>
	<p class="text-sm text-fg-muted">Uses the paused frame with your edits applied.</p>
{/if}
