<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import NodeCard from './NodeCard.svelte';
	import type { LaneTree } from '$lib/elixirs/lane';
	import type { ElixirDefinition, ElixirNode } from '$lib/elixirs/types';
	import { COLUMN_MAX, COLUMN_MIN, clampWidth, columnWidth, keyedWidth } from '$lib/elixirs/columns';
	import { PANE_HANDLE_GRIP, PANE_HANDLE_STRIP } from '$lib/generation/paneHandle';

	interface Props {
		definition: ElixirDefinition;
		tree: LaneTree;
		nodeData: (node: ElixirNode) => any;
		phone?: boolean;
		readOnly?: boolean;
		widths?: Array<number | null>;
		flashId?: string | null;
		labelOf: (id: string) => string;
		insertable: (id: string) => boolean;
		startable?: (id: string) => boolean;
		laneActive: (parent: string, child: string) => boolean;
		onwidth?: (index: number, width: number) => void;
		onwidthdone?: () => void;
		onnext: (anchor: HTMLElement, id: string) => void;
		oninsert: (anchor: HTMLElement, id: string) => void;
		onbefore?: (anchor: HTMLElement, id: string) => void;
		onstart: (anchor: HTMLElement) => void;
	}

	let {
		definition,
		tree,
		nodeData,
		phone = false,
		readOnly = false,
		widths = [],
		flashId = null,
		labelOf,
		insertable,
		startable = () => false,
		laneActive,
		onwidth,
		onwidthdone,
		onnext,
		oninsert,
		onbefore,
		onstart
	}: Props = $props();

	let board = $state<HTMLDivElement>();
	let fadeLeft = $state(false);
	let fadeRight = $state(false);
	let dragging = $state<{ index: number; x: number; start: number; pointer: number } | null>(null);

	let nodes = $derived(new Map(definition.nodes.map((node) => [node.id, node] as const)));
	let columns = $derived(Array.from({ length: Math.max(1, tree.columns) }, (_, index) => index));

	function width(index: number) {
		return columnWidth(widths, index);
	}

	function fades() {
		if (!board || phone) {
			fadeLeft = false;
			fadeRight = false;
			return;
		}
		const max = board.scrollWidth - board.clientWidth;
		fadeLeft = board.scrollLeft > 8;
		fadeRight = max > 8 && board.scrollLeft < max - 8;
	}

	$effect(() => {
		if (!board) return;
		const observer = new ResizeObserver(fades);
		observer.observe(board);
		if (board.firstElementChild) observer.observe(board.firstElementChild);
		fades();
		return () => observer.disconnect();
	});

	$effect(() => {
		widths;
		tree.columns;
		queueMicrotask(fades);
	});

	function startDrag(event: PointerEvent & { currentTarget: HTMLElement }, index: number) {
		if (event.button !== 0) return;
		event.preventDefault();
		event.currentTarget.setPointerCapture(event.pointerId);
		dragging = { index, x: event.clientX, start: width(index), pointer: event.pointerId };
	}

	function moveDrag(event: PointerEvent) {
		if (!dragging || event.pointerId !== dragging.pointer) return;
		onwidth?.(dragging.index, clampWidth(dragging.start + event.clientX - dragging.x));
	}

	function endDrag(event: PointerEvent) {
		if (!dragging || event.pointerId !== dragging.pointer) return;
		dragging = null;
		onwidthdone?.();
	}

	function keyResize(event: KeyboardEvent, index: number) {
		const next = keyedWidth(width(index), event.key, event.shiftKey);
		if (next === null) return;
		event.preventDefault();
		onwidth?.(index, next);
		onwidthdone?.();
	}

	function reset(index: number) {
		onwidth?.(index, columnWidth([], index));
		onwidthdone?.();
	}
</script>

{#snippet branch(id: string)}
	{@const node = nodes.get(id) as ElixirNode}
	{@const kids = tree.children.get(id) ?? []}
	{@const leaf = kids.length === 0}
	<div class="node" style="--cw: {width(tree.depth.get(id) ?? 0)}px" data-lane-node={id}>
		<div class="cw" class:open-end={leaf && (readOnly || phone)}>
			<NodeCard data={nodeData(node)} {phone} flash={flashId === id} />
			<i class="stub" aria-hidden="true"></i>
		</div>
		<div class="link" aria-hidden="true"></div>
		<div class="kids" class:leafkids={leaf}>
			{#each kids as kid (kid)}
				<div class="kid" class:lane={laneActive(id, kid)}>
					<i class="c c-tick" aria-hidden="true"></i>
					<i class="c c-up" aria-hidden="true"></i>
					<i class="c c-down" aria-hidden="true"></i>
					{#if !phone && !readOnly && insertable(kid)}
						<span class="ins">
							<Tooltip text={`Insert a step before ${labelOf(kid)}`}>
								<button type="button" class="ins-button" aria-label="Insert a step before {labelOf(kid)}" aria-haspopup="menu" onclick={(event) => oninsert(event.currentTarget, kid)}>
									<Icon name="plus" className="h-3.5 w-3.5" />
								</button>
							</Tooltip>
						</span>
					{/if}
					{@render branch(kid)}
				</div>
			{/each}
			{#if !readOnly}
				<div class="kid gk">
					<i class="c c-tick" aria-hidden="true"></i>
					<i class="c c-up" aria-hidden="true"></i>
					{#if leaf || phone}
						<button
							type="button"
							class="ghost"
							aria-haspopup="menu"
							aria-label="{leaf ? 'Add a next step after' : 'Add another branch after'} {labelOf(id)}"
							onclick={(event) => onnext(event.currentTarget, id)}
						>
							<Icon name="plus" className="h-3.5 w-3.5" />
							{leaf ? 'Next step' : 'Add a branch'}
						</button>
					{:else}
						<Tooltip text={`Add another branch after ${labelOf(id)}`}>
							<button
								type="button"
								class="ghost icon"
								aria-haspopup="menu"
								aria-label="Add another branch after {labelOf(id)}"
								onclick={(event) => onnext(event.currentTarget, id)}
							>
								<Icon name="plus" className="h-3.5 w-3.5" />
							</button>
						</Tooltip>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/snippet}

<div class="lane-wrap" class:phone>
	<div class="board" bind:this={board} onscroll={fades}>
		<div class="board-inner">
			{#if !phone && definition.nodes.length > 0}
				<div class="cols" aria-label="Columns">
					{#each columns as index (index)}
						<span class="col" style="width: {width(index)}px">
							<span class="col-name font-mono" aria-hidden="true">{index === 0 ? 'Start' : 'Then'}</span>
							<span class="col-rule" aria-hidden="true"></span>
							<span class="resize" class:active={dragging?.index === index}>
								<Tooltip text="Drag to resize column" wrapperClass="flex h-full">
									<span
										class="resize-handle {PANE_HANDLE_STRIP} cursor-col-resize"
										data-dragging={dragging?.index === index}
										role="separator"
										tabindex="0"
										aria-orientation="vertical"
										aria-label="Width of column {index + 1}"
										aria-valuemin={COLUMN_MIN}
										aria-valuemax={COLUMN_MAX}
										aria-valuenow={width(index)}
										onpointerdown={(event) => startDrag(event, index)}
										onpointermove={moveDrag}
										onpointerup={endDrag}
										onpointercancel={endDrag}
										ondblclick={() => reset(index)}
										onkeydown={(event) => keyResize(event, index)}
									><span class={PANE_HANDLE_GRIP} aria-hidden="true"></span></span>
								</Tooltip>
							</span>
						</span>
					{/each}
				</div>
			{/if}

			{#if definition.nodes.length === 0}
				<div class="empty rounded-lg border border-line bg-surface-1 p-5 shadow-raised">
					<h2 class="text-md font-semibold text-fg">An empty elixir</h2>
					<p class="mt-1 text-base text-fg-muted">
						Start with a preset step that makes images or video from a prompt, or with files you already have. Then add the steps that come after it.
					</p>
				</div>
			{:else}
				<div class="roots">
					{#each tree.roots as id (id)}
						<div class="root">
							{#if !phone && !readOnly && startable(id)}
								<span class="before">
									<Tooltip text={`Add a step before ${labelOf(id)}`}>
										<button type="button" class="ins-button" aria-label="Add a step before {labelOf(id)}" aria-haspopup="menu" onclick={(event) => onbefore?.(event.currentTarget, id)}>
											<Icon name="plus" className="h-3.5 w-3.5" />
										</button>
									</Tooltip>
								</span>
							{/if}
							{@render branch(id)}
						</div>
					{/each}
				</div>
			{/if}

			{#if !readOnly}
				<button type="button" class="ghost addroot" aria-haspopup="menu" onclick={(event) => onstart(event.currentTarget)}>
					<Icon name="plus" className="h-3.5 w-3.5" />
					Add a start
				</button>
			{/if}
		</div>
	</div>
	<div class="fade left" class:on={fadeLeft} aria-hidden="true"></div>
	<div class="fade right" class:on={fadeRight} aria-hidden="true"></div>
</div>

<style>
	.lane-wrap {
		--g: 88px;
		--h: 44px;
		--kg: 28px;
		--ty: 28px;
		position: relative;
		display: flex;
		min-width: 0;
		min-height: 0;
		flex: 1;
	}
	.board {
		min-width: 0;
		flex: 1;
		overflow: auto;
		padding: 0 32px 56px;
		scrollbar-width: auto;
		scrollbar-color: rgb(var(--line-hover)) rgb(var(--surface-1));
	}
	.board::-webkit-scrollbar {
		width: 12px;
		height: 12px;
	}
	.board::-webkit-scrollbar-track {
		background: rgb(var(--surface-1));
	}
	.board::-webkit-scrollbar-thumb {
		border: 2px solid rgb(var(--surface-1));
		border-radius: 4px;
		background: rgb(var(--line-hover));
	}
	.board-inner {
		width: max-content;
		min-width: 100%;
	}
	.fade {
		position: absolute;
		top: 0;
		bottom: 12px;
		z-index: 4;
		width: 72px;
		pointer-events: none;
		opacity: 0;
		transition: opacity 0.15s;
	}
	.fade.left {
		left: 0;
		background: linear-gradient(90deg, rgb(var(--canvas)), transparent);
	}
	.fade.right {
		right: 12px;
		background: linear-gradient(270deg, rgb(var(--canvas)), transparent);
	}
	.fade.on {
		opacity: 1;
	}
	.cols {
		position: sticky;
		top: 0;
		z-index: 3;
		display: flex;
		gap: var(--g);
		width: max-content;
		min-width: 100%;
		padding: 20px 0 16px;
		background: rgb(var(--canvas));
	}
	.col {
		position: relative;
		display: flex;
		flex: none;
		align-items: center;
		gap: 10px;
		font-size: 12px;
		line-height: 16px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}
	.col-rule {
		height: 1px;
		flex: 1;
		background: linear-gradient(90deg, rgb(var(--line-strong)), transparent);
	}
	.resize {
		position: absolute;
		top: -20px;
		bottom: -16px;
		right: calc((var(--g) - var(--h)) * -1 - 6px);
		display: flex;
	}
	.resize-handle {
		touch-action: none;
		border-left: 1px solid rgb(var(--line));
	}
	:global(:where(html:not([data-input='pointer']))) .resize-handle:focus-visible {
		outline: none;
		box-shadow: 0 0 0 2px rgb(var(--signal) / 0.35);
	}
	.roots {
		display: flex;
		width: max-content;
		flex-direction: column;
		align-items: flex-start;
		gap: 64px;
		padding-top: 4px;
	}
	.empty {
		max-width: 384px;
		margin-top: 24px;
	}
	.node {
		display: flex;
		align-items: flex-start;
	}
	.cw {
		position: relative;
		width: var(--cw);
		flex: none;
	}
	.open-end > .stub {
		display: none;
	}
	.root {
		position: relative;
	}
	.before {
		position: absolute;
		top: calc(var(--ty) - 12px);
		left: -30px;
		z-index: 2;
		opacity: 0.75;
		transition: opacity 0.1s;
	}
	.root:hover > .before,
	.before:focus-within {
		opacity: 1;
	}
	.stub {
		position: absolute;
		top: var(--ty);
		left: 100%;
		width: var(--h);
		height: 1px;
		background: rgb(var(--line-hover));
	}
	.link {
		width: var(--g);
		flex: none;
	}
	.kids {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: var(--kg);
	}
	.kid {
		position: relative;
	}
	.c {
		position: absolute;
		left: calc(var(--h) * -1);
		pointer-events: none;
		background: rgb(var(--line-hover));
	}
	.c-tick {
		top: var(--ty);
		width: var(--h);
		height: 1px;
	}
	.c-tick::after {
		content: '';
		position: absolute;
		top: -3px;
		right: 0;
		border-style: solid;
		border-color: transparent;
		border-width: 3px 0 4px 5px;
		border-left-color: rgb(var(--line-hover));
	}
	.c-up {
		top: calc(var(--kg) * -1);
		width: 1px;
		height: calc(var(--ty) + var(--kg));
	}
	.c-down {
		top: var(--ty);
		bottom: calc(var(--kg) * -1);
		width: 1px;
	}
	.kid:first-child > .c-up,
	.kid:last-child > .c-down {
		display: none;
	}
	.kid.lane > .c-tick {
		background: rgb(var(--signal));
	}
	.kid.lane > .c-tick::after {
		border-left-color: rgb(var(--signal));
	}
	.ins {
		position: absolute;
		top: calc(var(--ty) - 12px);
		left: calc(var(--h) * -1 - 12px);
		z-index: 2;
		opacity: 0;
		transition: opacity 0.1s;
	}
	.kid:hover > .ins,
	.ins:focus-within {
		opacity: 1;
	}
	.ins-button {
		display: grid;
		width: 24px;
		height: 24px;
		place-items: center;
		padding: 0;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
	}
	@media (hover: none) {
		.ins {
			display: none;
		}
	}
	.gk {
		padding-top: calc(var(--ty) - 16px);
	}
	.ghost {
		display: inline-flex;
		height: 32px;
		align-items: center;
		gap: 6px;
		padding: 0 10px;
		border: 1px dashed rgb(var(--line-hover));
		border-radius: 4px;
		background: transparent;
		color: rgb(var(--fg-subtle));
		font-size: 13px;
	}
	.ghost:hover {
		border-color: rgb(var(--fg-subtle));
		background: rgb(var(--surface-1));
		color: rgb(var(--fg));
	}
	.ghost.icon {
		width: 32px;
		justify-content: center;
		padding: 0;
	}
	:global(:where(html:not([data-input='pointer']))) .ghost:focus-visible,
	:global(:where(html:not([data-input='pointer']))) .ins-button:focus-visible {
		outline: 2px solid rgb(var(--signal));
		outline-offset: 2px;
	}
	.addroot {
		margin-top: 40px;
	}

	.phone {
		--ty: 26px;
	}
	.phone .board {
		padding: 16px 16px 32px;
		overflow-x: hidden;
	}
	.phone .board-inner {
		width: 100%;
	}
	.phone .roots {
		width: 100%;
		gap: 36px;
		padding-top: 0;
	}
	.phone .node {
		display: block;
		width: 100%;
	}
	.phone .cw {
		width: 100%;
	}
	.phone .stub,
	.phone .link,
	.phone .c-up,
	.phone .c-down {
		display: none;
	}
	.phone .kids {
		margin: 16px 0 0 8px;
		padding-left: 14px;
		border-left: 1px solid rgb(var(--line-hover));
		gap: 16px;
	}
	.phone .kid > .c-tick {
		left: -14px;
		width: 14px;
	}
	.phone .gk {
		padding-top: 0;
	}
	.phone .gk > .c {
		display: none;
	}
	.phone .kids.leafkids {
		margin: 8px 0 0;
		padding: 0;
		border: 0;
	}
	.phone .ghost {
		height: 40px;
		padding: 0 12px;
	}
	.phone .addroot {
		margin-top: 28px;
	}
</style>
