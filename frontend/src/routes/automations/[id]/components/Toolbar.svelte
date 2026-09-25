<script lang="ts">
	import { Button, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { automationEditor, canUndo, canRedo } from '$lib/stores/automationEditor';
	import { toasts } from '$lib/stores/toast';
	import { nodeDetail, toggleNodeDetail } from '$lib/automations/nodeDetail';

	let validating = $derived($automationEditor.validating);
	let undoable = $derived($canUndo);
	let redoable = $derived($canRedo);
	let detail = $derived($nodeDetail);

	async function handleValidate() {
		const issues = await automationEditor.validate();
		const errors = issues.filter((i) => i.severity === 'error');
		if (errors.length === 0) {
			toasts.success(issues.length === 0 ? 'Graph is valid' : 'No blocking errors');
		} else {
			toasts.warning(`${errors.length} validation error${errors.length === 1 ? '' : 's'} found`);
		}
	}

	function handleUndo() {
		automationEditor.undo();
	}

	function handleRedo() {
		automationEditor.redo();
	}

	function handleAutoLayout() {
		automationEditor.applyAutoLayout();
	}

	function handleToggleDetail() {
		toggleNodeDetail(detail);
	}
</script>

<div class="h-10 flex items-center justify-between gap-2 px-3 border-b border-line bg-surface-1 flex-shrink-0">
	<div class="flex items-center gap-1">
		<Tooltip text="Undo (Ctrl+Z)">
			<IconButton icon="undo" label="Undo" disabled={!undoable} onclick={handleUndo} />
		</Tooltip>
		<Tooltip text="Redo (Ctrl+Shift+Z)">
			<IconButton
				icon="undo"
				label="Redo"
				class="scale-x-[-1]"
				disabled={!redoable}
				onclick={handleRedo}
			/>
		</Tooltip>
		<Tooltip text="Auto-layout the graph">
			<IconButton icon="grid" label="Auto-layout" onclick={handleAutoLayout} />
		</Tooltip>
		<Tooltip text={detail === 'full' ? 'Switch to compact node cards' : 'Switch to full node cards'}>
			<IconButton
				icon="layers"
				label="Toggle node detail"
				active={detail === 'compact'}
				onclick={handleToggleDetail}
			/>
		</Tooltip>
	</div>

	<Button variant="ghost" size="sm" icon="check" onclick={handleValidate} loading={validating}>
		Validate
	</Button>
</div>
