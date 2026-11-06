import { confirmDialog } from '$lib/stores/confirm';
import { toasts } from '$lib/stores/toast';
import { moveItem } from '$lib/utils/reorder';
import { modelTypePresentation } from '$lib/utils/modelPresentation';
import { ModelRootsState, detectModelRoot } from '$lib/model-roots/state.svelte';
import { modelRootsErrorMessage } from '$lib/model-roots/errors';
import { orderedRoots, buildBindings, detectAgainApplies, missingSuggestions, suggestionKey, bindingTitle } from '$lib/model-roots/logic';
import type { ModelRoot, ModelRootBinding, ModelRootDetectionSuggestion } from '$lib/services/api/models';
import { bindingKey } from './derive';

export interface AgainResult {
	missing: ModelRootDetectionSuggestion[];
	ticks: Record<string, boolean>;
	note: string | null;
}

export class FoldersController {
	roots = new ModelRootsState();
	busy = $state<Record<string, boolean>>({});
	errors = $state<Record<string, string>>({});
	again = $state<Record<string, AgainResult>>({});
	addOpen = $state(false);
	addPath = $state('');
	selected = $state('');
	failures = $state(0);

	load() {
		return this.roots.load();
	}

	get overview() {
		return this.roots.overview;
	}

	get sortedRoots(): ModelRoot[] {
		return this.overview ? orderedRoots(this.overview.roots) : [];
	}

	get effective(): string {
		const roots = this.sortedRoots;
		const s = this.selected;
		if (s === 'routing' || s === 'indexing' || s === 'suggested' || roots.some((r) => r.id === s)) return s;
		return roots[0]?.id ?? 'routing';
	}

	isBusy(key: string) {
		return !!this.busy[key];
	}

	private async run<T>(key: string, fn: () => Promise<T>, failure: string): Promise<T | undefined> {
		this.busy = { ...this.busy, [key]: true };
		this.errors = { ...this.errors, [key]: '' };
		const fail = () => {
			const message = this.roots.error ?? failure;
			this.errors = { ...this.errors, [key]: message };
			this.failures += 1;
			toasts.error(message);
			return undefined;
		};
		try {
			const result = await fn();
			return result ? result : fail();
		} catch {
			return fail();
		} finally {
			this.busy = { ...this.busy, [key]: false };
		}
	}

	openAdd(path = '') {
		this.addPath = path;
		this.addOpen = true;
	}

	async saveRoot(root: ModelRoot, label: string, path: string) {
		const payload: { label?: string; path?: string } = {};
		if (label.trim() && label.trim() !== root.label) payload.label = label.trim();
		if (path.trim() && path.trim() !== root.path) payload.path = path.trim();
		if (!payload.label && !payload.path) return;
		const updated = await this.run(`save:${root.id}`, () => this.roots.update(root.id, payload), 'Failed to update the folder.');
		if (updated) toasts.success('Folder updated.');
	}

	async toggleReadOnly(root: ModelRoot) {
		await this.run(`ro:${root.id}`, () => this.roots.update(root.id, { read_only: !root.read_only }), 'Failed to update the folder.');
	}

	async toggleScan(root: ModelRoot, b: ModelRootBinding, enabled: boolean) {
		await this.run(`scan:${bindingKey(root.id, b)}`, () => this.roots.setBindingScan(root.id, b, enabled), 'Failed to change header detection.');
	}

	async probe(root: ModelRoot) {
		await this.run(`probe:${root.id}`, () => this.roots.probe(root.id), 'Failed to check the folder.');
		await this.roots.refresh();
	}

	async removeBinding(root: ModelRoot, b: ModelRootBinding) {
		const title = bindingTitle(b);
		const confirmed = await confirmDialog({
			title: 'Remove this type folder?',
			message: `Models under "${title}" in "${root.label}" become unavailable until you point that type at another folder. Files on disk are not touched.`,
			variant: 'danger'
		});
		if (!confirmed) return false;
		const done = await this.run(
			`rm:${bindingKey(root.id, b)}`,
			() => this.roots.update(root.id, { remove_bindings: [{ model_type: b.model_type, subdir: b.subdir }] }),
			'Failed to remove that folder.'
		);
		if (done) toasts.success(`Removed ${title} from "${root.label}".`);
		return !!done;
	}

	async makeDownloads(root: ModelRoot, b: ModelRootBinding) {
		await this.run(`dl:${bindingKey(root.id, b)}`, () => this.roots.setWrite(root.id, b.model_type, b.subdir), 'Failed to set the downloads folder.');
	}

	async deleteRoot(root: ModelRoot) {
		const confirmed = await confirmDialog({
			title: 'Delete this folder?',
			message: `Models in "${root.label}" become unavailable, not deleted - the files stay on disk. You can add this folder again later.`,
			variant: 'danger'
		});
		if (!confirmed) return false;
		const ok = await this.run(`del:${root.id}`, () => this.roots.remove(root.id), 'Failed to delete the folder.');
		if (ok) toasts.success(`Deleted "${root.label}".`);
		return !!ok;
	}

	async moveInType(type: string, order: string[], index: number, delta: number) {
		await this.run(`order:${type}`, () => this.roots.reorder(moveItem(order, index, index + delta), type), 'Failed to reorder folders.');
	}

	async detectAgain(root: ModelRoot) {
		this.busy = { ...this.busy, [`detect:${root.id}`]: true };
		const { detection, error } = await detectModelRoot(root.path);
		this.busy = { ...this.busy, [`detect:${root.id}`]: false };
		if (!detection) {
			this.again = { ...this.again, [root.id]: { missing: [], ticks: {}, note: error } };
			toasts.error(error ?? 'Failed to inspect that folder.');
			return;
		}
		if (!detectAgainApplies(root, detection)) {
			this.again = { ...this.again, [root.id]: { missing: [], ticks: {}, note: 'This folder belongs to a larger install, so nothing was compared.' } };
			return;
		}
		const missing = missingSuggestions(root, detection.suggestions);
		const ticks: Record<string, boolean> = {};
		for (const s of missing) ticks[suggestionKey(s)] = true;
		this.again = {
			...this.again,
			[root.id]: { missing, ticks, note: missing.length === 0 ? 'Nothing new found. Every detected folder is already added.' : null }
		};
	}

	dismissAgain(rootId: string) {
		const { [rootId]: _gone, ...rest } = this.again;
		this.again = rest;
	}

	async addMissing(root: ModelRoot) {
		const result = this.again[root.id];
		if (!result) return;
		const chosen = result.missing.filter((s) => result.ticks[suggestionKey(s)]);
		if (!chosen.length) return;
		const done = await this.run(
			`addmissing:${root.id}`,
			() => this.roots.update(root.id, { bindings: buildBindings(result.missing, result.ticks, {}, false).bindings }),
			'Failed to add those folders.'
		);
		if (done) {
			toasts.success(`Added ${chosen.length} folder${chosen.length === 1 ? '' : 's'} to "${root.label}".`);
			this.dismissAgain(root.id);
		}
	}

	async addFolderToRoot(root: ModelRoot, folder: { model_type: string; subdir: string }): Promise<string | null> {
		try {
			const updated = await this.roots.update(root.id, { bindings: [{ model_type: folder.model_type, subdir: folder.subdir, write: false }] });
			if (!updated) return this.roots.error ?? 'Failed to add that folder.';
			toasts.success(`Added a ${modelTypePresentation(folder.model_type).label} folder to "${root.label}".`);
			return null;
		} catch (e) {
			return modelRootsErrorMessage(e, 'Failed to add that folder.');
		}
	}

	handleCreated(root: ModelRoot) {
		toasts.success(`Added "${root.label}".`);
	}
}
