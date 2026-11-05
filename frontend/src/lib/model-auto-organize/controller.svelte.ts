import { confirmDialog } from '$lib/stores/confirm';
import { toasts } from '$lib/stores/toast';
import {
	draftFromParts,
	draftFromRule,
	effectiveKind,
	emptyDraft,
	type RuleDraft,
	type ValueLabels
} from '$lib/organize/draft';
import { parseOrganizeError } from '$lib/organize/errors';
import type { OrganizeStartRule } from '$lib/organize/handoff';
import { resolveModelNames } from '$lib/organize/labels';
import { attentionCount } from '$lib/organize/status';
import { organizeApiFor } from '$lib/organize/scope';
import { templatesFor } from '$lib/organize/templates';
import type { OrganizeCatalog, OrganizeRule, OrganizeSubject } from '$lib/types/organize';

export const AUTO_ORGANIZE_SUBJECT: OrganizeSubject = 'model';

export class AutoOrganizeController {
	catalog = $state<OrganizeCatalog | null>(null);
	rules = $state<OrganizeRule[]>([]);
	loaded = $state(false);
	loading = $state(false);
	loadError = $state<string | null>(null);
	busyId = $state<string | null>(null);
	query = $state('');
	modelNames = $state<Record<string, string>>({});
	editor = $state<{ key: number; draft: RuleDraft } | null>(null);
	resolvingRule = $state(false);
	activityRefresh = $state(0);

	private editorCounter = 0;

	private get api() {
		return organizeApiFor('instance');
	}

	editing = $derived(!!this.editor || this.resolvingRule);
	attention = $derived(attentionCount(this.rules));
	templates = $derived(templatesFor(AUTO_ORGANIZE_SUBJECT, this.catalog, 'instance'));
	labels = $derived<ValueLabels>({ models: this.modelNames, options: {} });
	visibleRules = $derived.by(() => {
		const q = this.query.trim().toLowerCase();
		return q ? this.rules.filter((r) => r.name.toLowerCase().includes(q)) : this.rules;
	});
	collectionNames = $derived.by(() => {
		const out: Record<string, string> = {};
		for (const rule of this.rules) for (const c of rule.targets.collections) out[c.id] = c.name;
		return out;
	});

	async load(): Promise<void> {
		this.loading = true;
		if (!this.catalog) {
			try {
				const response = await this.api.getOrganizeCatalog(AUTO_ORGANIZE_SUBJECT);
				if (response.success && response.data) this.catalog = response.data;
			} catch (err) {
				this.loadError = parseOrganizeError(err, 'Auto-organize could not be loaded.').message;
			}
		}
		try {
			const response = await this.api.listOrganizeRules(AUTO_ORGANIZE_SUBJECT);
			if (response.success) {
				this.rules = response.data ?? [];
				this.loadError = null;
				this.loaded = true;
			} else {
				this.loadError = response.message || 'The rules could not be loaded.';
			}
		} catch (err) {
			this.loadError = parseOrganizeError(err, 'The rules could not be loaded.').message;
		}
		await this.resolveLabels();
		this.loading = false;
	}

	private async resolveLabels(): Promise<void> {
		const catalog = this.catalog;
		if (!catalog) return;
		const ids: string[] = [];
		for (const rule of this.rules) {
			for (const cond of rule.conditions) {
				const spec = catalog.facts.find((f) => f.key === cond.fact);
				if (effectiveKind(spec) !== 'model_ref') continue;
				if (Array.isArray(cond.value)) ids.push(...(cond.value as string[]));
				else if (typeof cond.value === 'string') ids.push(cond.value);
			}
		}
		if (ids.length > 0) this.modelNames = { ...this.modelNames, ...(await resolveModelNames(ids)) };
	}

	openNew(pending: OrganizeStartRule | null): void {
		if (this.editor && this.editor.draft.id === null) return;
		const draft = pending
			? draftFromParts(AUTO_ORGANIZE_SUBJECT, pending)
			: emptyDraft(AUTO_ORGANIZE_SUBJECT);
		this.editorCounter += 1;
		this.editor = { key: this.editorCounter, draft };
	}

	async openExisting(id: string): Promise<boolean> {
		if (this.editor?.draft.id === id) return true;
		this.resolvingRule = true;
		try {
			const known = this.rules.find((r) => r.id === id);
			const rule = known ?? (await this.api.getOrganizeRule(id)).data;
			if (!rule) {
				toasts.error('That rule could not be found.');
				return false;
			}
			this.editorCounter += 1;
			this.editor = { key: this.editorCounter, draft: draftFromRule(rule) };
			return true;
		} catch {
			toasts.error('That rule could not be found.');
			return false;
		} finally {
			this.resolvingRule = false;
		}
	}

	closeEditor(): void {
		this.editor = null;
	}

	upsert = (rule: OrganizeRule): void => {
		const exists = this.rules.some((r) => r.id === rule.id);
		this.rules = exists ? this.rules.map((r) => (r.id === rule.id ? rule : r)) : [...this.rules, rule];
		void this.resolveLabels();
		this.activityRefresh += 1;
	};

	toggle = async (rule: OrganizeRule, next: boolean): Promise<void> => {
		this.busyId = rule.id;
		try {
			const response = await this.api.patchOrganizeRule(rule.id, { enabled: next });
			if (response.success && response.data) this.upsert(response.data);
			else toasts.error(response.message || 'The rule could not be changed.');
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		} finally {
			this.busyId = null;
		}
	};

	toggleStop = async (rule: OrganizeRule): Promise<void> => {
		try {
			const response = await this.api.patchOrganizeRule(rule.id, { stop_after: !rule.stop_after });
			if (response.success && response.data) this.upsert(response.data);
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		}
	};

	duplicate = async (rule: OrganizeRule): Promise<void> => {
		try {
			const response = await this.api.duplicateOrganizeRule(rule.id);
			if (response.success && response.data) {
				this.upsert(response.data);
				toasts.success(`Copied "${rule.name}". The copy is switched off.`);
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be copied.').message);
		}
	};

	remove = async (rule: OrganizeRule): Promise<void> => {
		const ok = await confirmDialog({
			title: `Delete "${rule.name}"?`,
			message: 'Models it already changed stay as they are, and its activity stays in the list.',
			variant: 'danger'
		});
		if (!ok) return;
		try {
			const response = await this.api.deleteOrganizeRule(rule.id);
			if (response.success) this.rules = this.rules.filter((r) => r.id !== rule.id);
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be deleted.').message);
		}
	};

	reorder = async (ids: string[]): Promise<void> => {
		if (this.query.trim()) return;
		const previous = this.rules;
		this.rules = ids.map((id) => previous.find((r) => r.id === id)!).filter(Boolean);
		try {
			const response = await this.api.reorderOrganizeRules(AUTO_ORGANIZE_SUBJECT, ids);
			this.rules = response.success && response.data ? response.data : previous;
		} catch (err) {
			this.rules = previous;
			toasts.error(parseOrganizeError(err, 'The order could not be saved.').message);
		}
	};
}
