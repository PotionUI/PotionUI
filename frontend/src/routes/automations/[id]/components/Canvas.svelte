<script lang="ts">
	/**
	 * `<SvelteFlow>` wrapper. `nodeTypes` routes the xyflow `type` field (which
	 * is the node "kind" — see `kindFromNodeType` in `automationEditor.ts`) to a
	 * single `AutomationNode` component, which derives its own handles from the
	 * catalog entry. Retheme is scoped to `.svelte-flow` below, overriding
	 * `--xy-*` vars with this app's design tokens.
	 *
	 * Local `$state.raw` node/edge arrays are what xyflow actually binds to
	 * and mutates (drag/connect/select); they're resynced from the
	 * `automationEditor` store on discrete graph-shape changes (load / palette
	 * add / delete / config edit, tracked via `$automationEditor.version`) and
	 * pushed back to the store via explicit event callbacks (drag-stop,
	 * connect, delete) rather than a blanket reactive effect, to avoid a
	 * resync <-> persist ping-pong loop.
	 *
	 * Selection is single-source-of-truth: xyflow owns `.selected` on the
	 * bound `nodes`/`edges` arrays (that's what its own `deleteKeyCode`
	 * handling reads — see @xyflow/svelte's KeyHandler.svelte), and we mirror
	 * it into `automationEditor.selectedNodeId` purely for the Inspector via
	 * `onselectionchange` (the ONE event both node-click-select and
	 * pane-click-deselect funnel through internally). We must never resync
	 * `nodes`/`edges` from the store in response to a plain selection change,
	 * or we'd stomp the `.selected` flag xyflow just set locally and delete
	 * would act on stale/previous selection — this is why the resync effect
	 * below gates on the version number actually changing (`writable` stores
	 * emit on every `update()` regardless of which field changed, so without
	 * this guard the effect reruns — and clobbers live selection — on every
	 * store write, not just graph-shape ones).
	 *
	 * Undo/redo obey the same rule: they bump `version` (so the restored graph
	 * IS resynced into xyflow), while `selectNode` still doesn't. Anything that
	 * only changes selection must never bump it.
	 */
	import {
		SvelteFlow,
		Background,
		BackgroundVariant,
		Controls,
		MiniMap,
		addEdge,
		useSvelteFlow,
		type Connection,
		type NodeTypes,
		type OnDelete,
		type OnSelectionChange
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/base.css';
	import AutomationNode from './nodes/AutomationNode.svelte';
	import {
		automationEditor,
		type FlowEdge,
		type FlowNode
	} from '$lib/stores/automationEditor';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { setNodeCount } from '$lib/automations/nodeDetail';
	import { PALETTE_DRAG_MIME } from './paletteDrag';

	// Requires an ancestor `<SvelteFlowProvider>` — set up one level up in
	// `[id]/+page.svelte` so this component (a descendant) can resolve it.
	// `useSvelteFlow()` in v1.6 has no `setNodes` — the node array is owned by
	// `bind:nodes` below, so layout/undo go through the store + a version bump.
	const { screenToFlowPosition, fitView } = useSvelteFlow();

	// One component renders every kind; the xyflow `type` field carries the kind
	// and the component derives its handles from the catalog entry.
	const nodeTypes: NodeTypes = {
		trigger: AutomationNode,
		condition: AutomationNode,
		action: AutomationNode
	};

	let nodes = $state.raw<FlowNode[]>([]);
	let edges = $state.raw<FlowEdge[]>([]);

	// A plain `writable` store notifies subscribers on every `update()` call,
	// not only when `.version` itself changed value — so without this guard
	// `$automationEditor` re-triggers the effect (and the resync below) on
	// every store write, including the selection-only writes from
	// `handleSelectionChange`, clobbering xyflow's own live `.selected` state.
	let lastSyncedVersion = -1;

	$effect(() => {
		const version = $automationEditor.version;
		if (version === lastSyncedVersion) return;
		lastSyncedVersion = version;
		nodes = $automationEditor.nodes;
		edges = $automationEditor.edges;
	});

	// Node cards collapse to compact above a threshold; this is view state and
	// deliberately never touches `version`.
	$effect(() => {
		setNodeCount($automationEditor.nodes.length);
	});

	// Re-fit after an auto-layout moved everything. Keyed on `layoutTick` rather
	// than `version` so ordinary edits don't yank the viewport around.
	let lastLayoutTick = 0;
	$effect(() => {
		const tick = $automationEditor.layoutTick;
		if (tick === lastLayoutTick) return;
		lastLayoutTick = tick;
		// Let the resync above flush into xyflow's bound array first.
		queueMicrotask(() => fitView({ duration: 200 }));
	});

	const handleSelectionChange: OnSelectionChange<FlowNode, FlowEdge> = ({ nodes: selected }) => {
		automationEditor.selectNode(selected[0]?.id ?? null);
	};

	// `record: true` marks the user-meaningful mutations, making them undoable.
	function handleNodeDragStop() {
		automationEditor.setNodes(nodes, { record: true });
	}

	function handleConnect(connection: Connection) {
		const next = addEdge(connection, edges);
		automationEditor.setEdges(next, { record: true });
		edges = next;
	}

	const handleDelete: OnDelete<FlowNode, FlowEdge> = () => {
		// One undo entry for the whole delete, not one per array.
		automationEditor.setNodes(nodes, { record: true });
		automationEditor.setEdges(edges);
	};

	/** Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z (or Ctrl+Y) redo — but never while the
	 *  user is typing in an Inspector field, and never stealing xyflow's
	 *  Backspace/Delete handling. */
	function handleKeydown(event: KeyboardEvent) {
		if (!(event.ctrlKey || event.metaKey) || event.altKey) return;

		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;

		const key = event.key.toLowerCase();
		if (key === 'z') {
			event.preventDefault();
			if (event.shiftKey) automationEditor.redo();
			else automationEditor.undo();
		} else if (key === 'y') {
			event.preventDefault();
			automationEditor.redo();
		}
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		const key = event.dataTransfer?.getData(PALETTE_DRAG_MIME);
		if (!key) return;
		const nodeTypeDef = $automationNodeTypes.find((t) => t.key === key);
		if (!nodeTypeDef) return;
		const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
		automationEditor.addNodeFromPalette(nodeTypeDef, position);
	}

	function handleDragOver(event: DragEvent) {
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	class="automation-canvas flex-1 min-w-0"
	ondrop={handleDrop}
	ondragover={handleDragOver}
	role="application"
>
	<SvelteFlow
		bind:nodes
		bind:edges
		{nodeTypes}
		proOptions={{ hideAttribution: true }}
		minZoom={0.2}
		maxZoom={2}
		fitView
		deleteKey={['Backspace', 'Delete']}
		onselectionchange={handleSelectionChange}
		onnodedragstop={handleNodeDragStop}
		onconnect={handleConnect}
		ondelete={handleDelete}
	>
		<Background variant={BackgroundVariant.Dots} gap={20} size={1} />
		<Controls showLock={false} />
		<MiniMap pannable zoomable />
	</SvelteFlow>
</div>

<style>
	.automation-canvas {
		height: 100%;
		--xy-background-color: rgb(var(--canvas));
		--xy-background-pattern-color: rgb(var(--line-strong));
		--xy-edge-stroke: rgb(var(--line-strong));
		--xy-edge-stroke-selected: rgb(var(--signal));
		--xy-edge-stroke-width: 1.5px;
		--xy-connectionline-stroke: rgb(var(--signal));
		--xy-node-border: 1px solid transparent;
		--xy-node-boxshadow-selected: 0 0 0 2px rgb(var(--signal));
		--xy-handle-background-color: rgb(var(--surface-3));
		--xy-handle-border-color: rgb(var(--line-hover));
		--xy-minimap-background-color: rgb(var(--surface-1));
		--xy-controls-button-background-color: rgb(var(--surface-2));
		--xy-controls-button-background-color-hover: rgb(var(--surface-3));
		--xy-controls-button-color: rgb(var(--fg-muted));
		--xy-controls-button-color-hover: rgb(var(--fg));
		--xy-controls-button-border-color: rgb(var(--line-strong));
	}

	.automation-canvas :global(.svelte-flow__handle) {
		width: 8px;
		height: 8px;
		border-radius: 2px;
	}

	.automation-canvas :global(.svelte-flow__node) {
		cursor: grab;
	}

	.automation-canvas :global(.svelte-flow__minimap) {
		border: 1px solid rgb(var(--line-strong));
		border-radius: 6px;
	}

	/* Minimap nodes are achromatic chrome; the selection mask is the only state colour. */
	.automation-canvas :global(.svelte-flow__minimap-node) {
		fill: rgb(var(--surface-3));
		stroke: rgb(var(--line-hover));
	}

	.automation-canvas :global(.svelte-flow__minimap-node.selected) {
		fill: rgb(var(--signal));
	}

	.automation-canvas :global(.svelte-flow__minimap-mask) {
		fill: rgb(var(--canvas) / 0.6);
	}
</style>
