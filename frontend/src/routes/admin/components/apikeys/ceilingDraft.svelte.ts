import { api } from '$lib/services/api/index';
import type { McpToolsListing } from '$lib/services/api/mcpTools';
import { applyChange, type ToolsChange } from '$lib/components/apikeys/mcpToolsView';
import { ceilingLowered, ceilingPatchOf, mergeChange, pendingCount } from './accessPanel';

export class CeilingDraft {
	base = $state<McpToolsListing | null>(null);
	change = $state<ToolsChange>({});
	loading = $state(true);
	failed = $state(false);
	unavailable = $state(false);
	saving = $state(false);

	listing = $derived(this.base ? applyChange(this.base, this.change) : null);
	count = $derived(pendingCount(this.base, this.change));
	lowered = $derived(ceilingLowered(this.base, this.change));

	async load(): Promise<void> {
		this.loading = true;
		this.failed = false;
		try {
			const response = await api.getMcpCeiling();
			if (response.success && response.data) {
				this.base = response.data;
				this.change = {};
				this.unavailable = false;
			} else {
				this.failed = true;
			}
		} catch (err) {
			if ((err as { response?: { status?: number } })?.response?.status === 404) this.unavailable = true;
			else this.failed = true;
		} finally {
			this.loading = false;
		}
	}

	commit(change: ToolsChange): void {
		this.change = mergeChange(this.change, change);
	}

	discard(): void {
		this.change = {};
	}

	async save(): Promise<boolean> {
		if (!this.base) return true;
		const patch = ceilingPatchOf(this.base, this.change);
		if (!patch) {
			this.change = {};
			return true;
		}
		this.saving = true;
		try {
			const response = await api.patchMcpCeiling(patch);
			if (!response.success || !response.data) return false;
			this.base = response.data;
			this.change = {};
			return true;
		} finally {
			this.saving = false;
		}
	}
}
