<script lang="ts">
	// `.seam` / `.join-block` from console.html + shot-states.html. The join's
	// `kind` only decides the icon/tone; every other word on screen (`label`,
	// `sentence`, the chip/toggle text) comes straight off the ConsoleJoin
	// model -- consoleModel.ts already knows which sentence and which control
	// a given profile/capability combination gets (D3/D4 in PLAN.md).
	//
	// `onGeneratePreviousAndThis`/`onConvertToFreshCut` back the missing-
	// predecessor block's two actions (PLAN.md §C W3 -- ShotConsole now
	// produces a `kind: 'missing'` join once a checked shot's predecessor has
	// no 'done' run in the runs map). Defaults stay no-ops so the variant
	// still renders (and is testable) without a caller having to wire both.
	import type { ConsoleJoin } from './consoleModel';
	import { joinKindMeta } from './badgeMeta';
	import ConsoleIcon from './ConsoleIcon.svelte';

	let {
		join,
		onSetJoin,
		onSetOverlap = () => {},
		onSetStitch = () => {},
		maxOverlapFrames = null,
		stitch = true,
		source = null,
		onGeneratePreviousAndThis = () => {},
		onConvertToFreshCut = () => {}
	}: {
		join: ConsoleJoin;
		onSetJoin: (afterShotId: string, value: 'continue' | 'cut') => void;
		/** `chain.continuation.overlap_frames` is one document-wide number:
		 * editing it on any continuing join changes every continuing join. */
		onSetOverlap?: (frames: number) => void;
		onSetStitch?: (stitch: boolean) => void;
		maxOverlapFrames?: number | null;
		stitch?: boolean;
		source?: 'tail_frames' | 'last_frame' | null;
		/** `kind: 'missing'` only -- the contiguous span back to the nearest
		 * fresh cut, in film order, inclusive of the checked shot
		 * (`join.control.spanShotIds`). */
		onGeneratePreviousAndThis?: (spanShotIds: string[]) => void;
		onConvertToFreshCut?: (afterShotId: string) => void;
	} = $props();

	let meta = $derived(joinKindMeta(join.kind));
	let expanded = $state(false);
	let editable = $derived(join.overlapFrames != null);

	function commitOverlap(e: Event) {
		const input = e.currentTarget as HTMLInputElement;
		const frames = Math.round(parseFloat(input.value));
		if (!Number.isFinite(frames) || frames < 0) {
			input.value = String(join.overlapFrames ?? 0);
			return;
		}
		const clamped = maxOverlapFrames != null ? Math.min(frames, maxOverlapFrames) : frames;
		input.value = String(clamped);
		if (clamped !== join.overlapFrames) onSetOverlap(clamped);
	}
</script>

<!-- Maintainer ruling (09-04): the vertical connector renders BEHIND the join
	block (z-0 line under a z-10, OPAQUE block -- `bg-surface-2/55` used to let
	the line show through). Two arrowheads, each with its tip exactly on the
	edge it points at and fully inside the 24px gap so no card overlaps it:
	card -> arrow -> block -> arrow -> card. -->
{#snippet arrow(placement: string)}
	<svg class="absolute left-1/2 z-0 -translate-x-1/2 {placement}" width="9" height="24" viewBox="0 0 9 24" aria-hidden="true">
		<line x1="4.5" y1="0" x2="4.5" y2="18" class="stroke-line-strong" stroke-width="1" />
		<path d="M0.5 17.5H8.5L4.5 23.5Z" class="fill-fg-subtle" />
	</svg>
{/snippet}
<div class="relative my-6 flex justify-center">
	{@render arrow('-top-6')}
	{@render arrow('-bottom-6')}
	{#if join.kind === 'missing'}
		<div
			class="relative z-10 flex min-h-[34px] w-3/5 max-w-[680px] flex-wrap items-center gap-3 rounded-md border border-warning/40 bg-surface-2 px-3 py-1.5"
		>
			<span class="flex flex-none items-center gap-[5px] whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.04em] text-warning">
				<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px]" />
				{join.label}
			</span>
			<span class="min-w-0 flex-1 whitespace-normal text-xs text-fg">{join.sentence}</span>
			<div class="flex w-full flex-none flex-wrap gap-2">
				<button
					type="button"
					class="inline-flex h-[26px] items-center justify-center gap-1.5 rounded bg-accent px-3 text-[11.5px] font-semibold text-accent-contrast"
					onclick={() =>
						onGeneratePreviousAndThis(
							join.control.kind === 'missing' ? join.control.spanShotIds : [join.afterShotId, join.beforeShotId]
						)}
				>
					Generate previous + this shot
				</button>
				<button
					type="button"
					class="inline-flex h-[26px] items-center gap-1.5 rounded border border-line-strong bg-surface-2 px-2.5 text-[11.5px] text-fg"
					onclick={() => onConvertToFreshCut(join.afterShotId)}
				>
					Convert to fresh cut
				</button>
			</div>
		</div>
	{:else}
		<div class="relative z-10 w-3/5 min-w-[300px] max-w-[680px] rounded-md border border-line bg-surface-2">
		<!-- svelte-ignore a11y_no_static_element_interactions, a11y_no_noninteractive_tabindex -->
		<div
			class="flex min-h-[34px] items-center gap-3 px-3 py-1.5 {editable ? 'cursor-pointer' : ''}"
			role={editable ? 'button' : undefined}
			tabindex={editable ? 0 : undefined}
			aria-expanded={editable ? expanded : undefined}
			onclick={() => editable && (expanded = !expanded)}
			onkeydown={(e) => {
				if (editable && e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
					e.preventDefault();
					expanded = !expanded;
				}
			}}
		>
			<span class="flex flex-none items-center gap-[5px] whitespace-nowrap font-mono text-[10.5px] uppercase tracking-[0.04em] text-fg-muted">
				<ConsoleIcon name={meta.icon} class="h-[11px] w-[11px] {meta.iconClass}" />
				{join.label}
			</span>
			<span class="min-w-0 flex-1 truncate text-xs text-fg-muted">{join.sentence}</span>
			<div class="flex flex-none items-center gap-2">
				{#if join.control.kind === 'chip'}
					<span
						class="whitespace-nowrap font-mono text-[10px] uppercase tracking-[0.03em] {meta.chipTone === 'ready'
							? 'text-success'
							: 'text-fg-subtle'}"
					>
						{join.control.text}
					</span>
				{:else if join.control.kind === 'toggle'}
					<div class="inline-flex items-stretch overflow-hidden rounded border border-line-strong bg-surface-1">
						<button
							type="button"
							class="flex h-5 items-center gap-1 whitespace-nowrap border-r border-line-strong px-2 font-mono text-[10px] uppercase tracking-[0.03em] {join
								.control.value === 'continue'
								? 'bg-signal/10 text-signal'
								: 'text-fg-subtle'}"
							onclick={(e) => {
								e.stopPropagation();
								onSetJoin(join.afterShotId, 'continue');
							}}
						>
							<ConsoleIcon name="link" class="h-[9px] w-[9px]" />
							Continue
						</button>
						<button
							type="button"
							class="flex h-5 items-center gap-1 whitespace-nowrap px-2 font-mono text-[10px] uppercase tracking-[0.03em] {join.control
								.value === 'cut'
								? 'bg-signal/10 text-signal'
								: 'text-fg-subtle'}"
							onclick={(e) => {
								e.stopPropagation();
								onSetJoin(join.afterShotId, 'cut');
							}}
						>
							<ConsoleIcon name="scissors" class="h-[9px] w-[9px]" />
							Fresh cut
						</button>
					</div>
				{/if}
				{#if editable}
					<button
						type="button"
						class="flex h-5 w-5 flex-none items-center justify-center rounded text-fg-subtle hover:bg-surface-3 hover:text-fg"
						aria-label={expanded ? 'Hide join settings' : 'Show join settings'}
						aria-expanded={expanded}
						onclick={(e) => {
							e.stopPropagation();
							expanded = !expanded;
						}}
					>
						<ConsoleIcon name="chevron-down" class="h-3 w-3 transition-transform {expanded ? '' : '-rotate-90'}" />
					</button>
				{/if}
			</div>
		</div>
		{#if editable && expanded}
			<div class="flex flex-wrap items-end gap-4 border-t border-line px-3 py-2.5">
				<label class="flex flex-col gap-1">
					<span class="font-mono text-[10px] uppercase tracking-[0.04em] text-fg-subtle">Overlap</span>
					<span class="flex items-center gap-1.5">
						<input
							type="number"
							min="0"
							max={maxOverlapFrames ?? undefined}
							step="1"
							value={join.overlapFrames}
							class="h-[27px] w-[72px] rounded border border-line-strong bg-surface-1 px-2 text-right font-mono text-xs tabular-nums text-fg focus:outline-none focus:ring-1 focus:ring-signal"
							aria-label="Overlap frames"
							onchange={commitOverlap}
							onkeydown={(e) => {
								if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur();
							}}
						/>
						<span class="font-mono text-[10px] text-fg-subtle">frames{maxOverlapFrames != null ? ` · max ${maxOverlapFrames}` : ''}</span>
					</span>
				</label>
				<label class="flex h-[27px] items-center gap-2 font-mono text-[10px] uppercase tracking-[0.04em] text-fg-subtle">
					<input type="checkbox" checked={stitch} aria-label="Stitch into one clip" onchange={(e) => onSetStitch((e.currentTarget as HTMLInputElement).checked)} />
					Stitch into one clip
				</label>
				{#if source}
					<span class="flex h-[27px] items-center font-mono text-[10px] uppercase tracking-[0.04em] text-fg-subtle">
						Source · {source === 'tail_frames' ? 'tail frames' : 'last frame'}
					</span>
				{/if}
				<span class="basis-full text-[11px] text-fg-subtle">Overlap and stitch apply to every continuing join in this film.</span>
			</div>
		{/if}
		</div>
	{/if}
</div>
