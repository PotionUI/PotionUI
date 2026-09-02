import { logger } from '$lib/utils/logger';
import { writable, derived, get } from 'svelte/store';
import { api, type PhrasebookStateFilter } from '$lib/services/api/index';
import { confirmDialog } from './confirm';

export interface CategoryWithChildren {
	id: string;
	name: string;
	path: string;
	parent_id?: string | null;
	description: string;
	is_active: boolean;
	created_at: string;
	updated_at: string;
	user_id?: string;
	children?: CategoryWithChildren[];
	childrenLoaded?: boolean;
}

export interface PhrasebookValue {
	id: string;
	category_id: string;
	label: string;
	value: string;
	sort_order: number;
	is_active: boolean;
	preview_file_id?: string;
	preview_generation_id?: string;
	created_at: string;
	updated_at: string;
}

export interface CategoryForm {
	name: string;
	path: string;
	parent_id: string | null;
	description: string;
}

export interface ValueForm {
	category_id: string;
	label: string;
	value: string;
	sort_order: number;
}

export type EditMode = 'none' | 'category' | 'value' | 'new-category' | 'new-value';

export interface PhrasebookState {
	categories: Record<string, CategoryWithChildren>;
	rootCategoryIds: string[];
	allCategories: CategoryWithChildren[];
	categoryValues: Record<string, PhrasebookValue[]>;
	stateFilter: PhrasebookStateFilter;
	expandedCategories: Set<string>;
	loadingCategories: Set<string>;
	selectedCategoryId: string | null;
	selectedValueId: string | null;
	isLoading: boolean;
	valuesLoading: boolean;
	editMode: EditMode;
	categoryForm: CategoryForm;
	valueForm: ValueForm;
	selectedValueIds: Set<string>;
}

const initialState: PhrasebookState = {
	categories: {},
	rootCategoryIds: [],
	allCategories: [],
	categoryValues: {},
	stateFilter: 'all',
	expandedCategories: new Set(),
	loadingCategories: new Set(),
	selectedCategoryId: null,
	selectedValueId: null,
	isLoading: false,
	valuesLoading: false,
	editMode: 'none',
	categoryForm: { name: '', path: '', parent_id: null, description: '' },
	valueForm: { category_id: '', label: '', value: '', sort_order: 0 },
	selectedValueIds: new Set()
};

function createPhrasebookStore() {
	const { subscribe, set, update } = writable<PhrasebookState>(initialState);

	function state() {
		return get({ subscribe });
	}

	return {
		subscribe,

		setStateFilter(filter: PhrasebookStateFilter) {
			update((s) => ({ ...s, stateFilter: filter }));
		},

		async loadRootCategories() {
			update((s) => ({ ...s, isLoading: true }));
			try {
				const response = await api.getPhrasebookCategories(true, state().stateFilter);
				if (response.success && response.data) {
					const newCategories: Record<string, CategoryWithChildren> = {};
					const rootIds: string[] = [];
					response.data.categories.forEach((cat: any) => {
						newCategories[cat.id] = { ...cat, childrenLoaded: false };
						rootIds.push(cat.id);
					});
					update((s) => ({ ...s, categories: newCategories, rootCategoryIds: rootIds }));
				}
			} catch (error) {
				logger.error('Failed to load categories:', error);
			} finally {
				update((s) => ({ ...s, isLoading: false }));
			}
		},

		async loadAllCategories() {
			try {
				const response = await api.getPhrasebookCategories(false, state().stateFilter);
				if (response.success && response.data) {
					const allCategories = response.data.categories;
					update((s) => ({ ...s, allCategories }));
				}
			} catch (error) {
				logger.error('Failed to load all categories:', error);
			}
		},

		async handleStateFilterChange() {
			update((s) => ({
				...s,
				selectedCategoryId: null,
				selectedValueId: null,
				editMode: 'none',
				categoryValues: {}
			}));
			await this.loadRootCategories();
			await this.loadAllCategories();
		},

		async loadCategoryChildren(categoryId: string) {
			update((s) => ({ ...s, loadingCategories: new Set([...s.loadingCategories, categoryId]) }));
			try {
				const response = await api.getCategoryChildren(categoryId);
				if (response.success && response.data) {
					const childCategories: CategoryWithChildren[] = response.data.categories.map((cat: any) => ({
						...cat,
						childrenLoaded: false
					}));
					update((s) => ({
						...s,
						categories: {
							...s.categories,
							...Object.fromEntries(childCategories.map((c) => [c.id, c])),
							[categoryId]: {
								...s.categories[categoryId],
								children: childCategories,
								childrenLoaded: true
							}
						}
					}));
				}
			} catch (error) {
				logger.error('Failed to load category children:', error);
			} finally {
				update((s) => ({
					...s,
					loadingCategories: new Set([...s.loadingCategories].filter((id) => id !== categoryId))
				}));
			}
		},

		async loadCategoryValues(categoryId: string) {
			update((s) => ({ ...s, valuesLoading: true }));
			try {
				const response = await api.getPhrasebookCategory(categoryId);
				if (response.success && response.data) {
					const values = response.data.values;
					update((s) => ({ ...s, categoryValues: { ...s.categoryValues, [categoryId]: values } }));
				}
			} catch (error) {
				logger.error('Failed to load category values:', error);
			} finally {
				update((s) => ({ ...s, valuesLoading: false }));
			}
		},

		// Expand tree path to a specific category (used to restore selection from URL)
		async expandPathToCategory(categoryId: string) {
			const s0 = state();
			const targetCategory = s0.allCategories.find((c) => c.id === categoryId);
			if (!targetCategory) return;

			const pathToExpand: string[] = [];
			let currentId = targetCategory.parent_id;
			while (currentId) {
				pathToExpand.unshift(currentId);
				const parent = s0.allCategories.find((c) => c.id === currentId);
				currentId = parent?.parent_id || null;
			}

			for (const nodeId of pathToExpand) {
				update((s) =>
					s.expandedCategories.has(nodeId)
						? s
						: { ...s, expandedCategories: new Set([...s.expandedCategories, nodeId]) }
				);
				if (!state().categories[nodeId]?.childrenLoaded) {
					await this.loadCategoryChildren(nodeId);
				}
			}

			update((s) =>
				s.expandedCategories.has(categoryId)
					? s
					: { ...s, expandedCategories: new Set([...s.expandedCategories, categoryId]) }
			);
		},

		handleToggleCategory(categoryId: string) {
			const s0 = state();
			const isExpanded = s0.expandedCategories.has(categoryId);
			if (isExpanded) {
				update((s) => ({
					...s,
					expandedCategories: new Set([...s.expandedCategories].filter((id) => id !== categoryId))
				}));
			} else {
				update((s) => ({ ...s, expandedCategories: new Set([...s.expandedCategories, categoryId]) }));
				const category = s0.categories[categoryId];
				if (category && !category.childrenLoaded) {
					this.loadCategoryChildren(categoryId);
				}
			}
		},

		handleSelectCategory(categoryId: string) {
			update((s) => ({ ...s, selectedCategoryId: categoryId, selectedValueId: null, editMode: 'none' }));
			const s0 = state();
			if (!s0.categoryValues[categoryId]) {
				this.loadCategoryValues(categoryId);
			}
			if (!s0.expandedCategories.has(categoryId)) {
				const category = s0.categories[categoryId];
				if (category && !category.childrenLoaded) {
					this.loadCategoryChildren(categoryId);
				}
				update((s) => ({ ...s, expandedCategories: new Set([...s.expandedCategories, categoryId]) }));
			}
		},

		handleSelectValue(valueId: string) {
			const s0 = state();
			const values = s0.selectedCategoryId ? s0.categoryValues[s0.selectedCategoryId] || [] : [];
			const value = values.find((v) => v.id === valueId);
			if (value) {
				update((s) => ({
					...s,
					selectedValueId: valueId,
					valueForm: {
						category_id: value.category_id,
						label: value.label,
						value: value.value,
						sort_order: value.sort_order
					},
					editMode: 'value'
				}));
			}
		},

		async selectCategoryFromFind(categoryId: string) {
			await this.expandPathToCategory(categoryId);
			this.handleSelectCategory(categoryId);
		},

		async selectValueFromFind(categoryId: string, valueId: string) {
			await this.expandPathToCategory(categoryId);
			update((s) => ({
				...s,
				selectedCategoryId: categoryId,
				selectedValueId: null,
				editMode: 'none',
				expandedCategories: new Set([...s.expandedCategories, categoryId])
			}));
			if (!state().categoryValues[categoryId]) {
				await this.loadCategoryValues(categoryId);
			}
			this.handleSelectValue(valueId);
		},

		async updateValueText(
			target: { id: string; category_id: string; label: string; sort_order: number },
			text: string
		): Promise<boolean> {
			try {
				const response = await api.updatePhrasebookValue(target.id, {
					category_id: target.category_id,
					label: target.label,
					value: text,
					sort_order: target.sort_order
				});
				if (!response.success) return false;
			} catch (error) {
				logger.error('Failed to update value text:', error);
				return false;
			}
			if (state().categoryValues[target.category_id]) {
				await this.loadCategoryValues(target.category_id);
			}
			update((s) =>
				s.selectedValueId === target.id ? { ...s, valueForm: { ...s.valueForm, value: text } } : s
			);
			return true;
		},

		handleEditCategory() {
			const s0 = state();
			const selectedCategory = s0.selectedCategoryId ? s0.categories[s0.selectedCategoryId] : null;
			if (!selectedCategory) return;
			update((s) => ({
				...s,
				categoryForm: {
					name: selectedCategory.name,
					path: selectedCategory.path,
					parent_id: selectedCategory.parent_id || null,
					description: selectedCategory.description || ''
				},
				editMode: 'category'
			}));
		},

		handleNewCategory() {
			const s0 = state();
			update((s) => ({
				...s,
				categoryForm: { name: '', path: '', parent_id: s0.selectedCategoryId, description: '' },
				editMode: 'new-category'
			}));
		},

		handleNewValue() {
			const s0 = state();
			if (!s0.selectedCategoryId) return;
			const values = s0.categoryValues[s0.selectedCategoryId] || [];
			update((s) => ({
				...s,
				valueForm: { category_id: s0.selectedCategoryId as string, label: '', value: '', sort_order: values.length },
				editMode: 'new-value'
			}));
		},

		async handleSaveCategory() {
			const s0 = state();
			try {
				if (s0.editMode === 'new-category') {
					const response = await api.createPhrasebookCategory(s0.categoryForm);
					if (response.success) {
						await this.loadRootCategories();
						await this.loadAllCategories();
						if (s0.categoryForm.parent_id) {
							await this.loadCategoryChildren(s0.categoryForm.parent_id);
						}
						update((s) => ({ ...s, editMode: 'none' }));
					}
				} else if (s0.editMode === 'category' && s0.selectedCategoryId) {
					const response = await api.updatePhrasebookCategory(s0.selectedCategoryId, s0.categoryForm);
					if (response.success) {
						await this.loadRootCategories();
						await this.loadAllCategories();
						update((s) => ({ ...s, editMode: 'none' }));
					}
				}
			} catch (error) {
				logger.error('Failed to save category:', error);
			}
		},

		async handleDeleteCategory() {
			const s0 = state();
			if (!s0.selectedCategoryId) return;
			if (
				!(await confirmDialog({
					title: 'Delete category',
					message: 'Delete this category and all its values?',
					variant: 'danger'
				}))
			)
				return;
			try {
				const response = await api.deletePhrasebookCategory(s0.selectedCategoryId);
				if (response.success) {
					await this.loadRootCategories();
					await this.loadAllCategories();
					update((s) => ({ ...s, selectedCategoryId: null, editMode: 'none' }));
				}
			} catch (error) {
				logger.error('Failed to delete category:', error);
			}
		},

		async handleSaveValue() {
			const s0 = state();
			try {
				if (s0.editMode === 'new-value') {
					const response = await api.createPhrasebookValue(s0.valueForm);
					if (response.success && s0.selectedCategoryId) {
						await this.loadCategoryValues(s0.selectedCategoryId);
						update((s) => ({ ...s, editMode: 'none', selectedValueId: null }));
					}
				} else if (s0.editMode === 'value' && s0.selectedValueId) {
					const response = await api.updatePhrasebookValue(s0.selectedValueId, s0.valueForm);
					if (response.success && s0.selectedCategoryId) {
						await this.loadCategoryValues(s0.selectedCategoryId);
						update((s) => ({ ...s, editMode: 'none' }));
					}
				}
			} catch (error) {
				logger.error('Failed to save value:', error);
			}
		},

		async handleDeleteValue() {
			const s0 = state();
			if (!s0.selectedValueId || !s0.selectedCategoryId) return;
			if (
				!(await confirmDialog({
					title: 'Delete value',
					message: 'Delete this value?',
					variant: 'danger'
				}))
			)
				return;
			try {
				const response = await api.deletePhrasebookValue(s0.selectedValueId);
				if (response.success) {
					await this.loadCategoryValues(s0.selectedCategoryId);
					update((s) => ({ ...s, selectedValueId: null, editMode: 'none' }));
				}
			} catch (error) {
				logger.error('Failed to delete value:', error);
			}
		},

		handleCancelEdit() {
			update((s) => ({ ...s, editMode: 'none' }));
		},

		async handleToggleCategoryActive(categoryId: string) {
			const category = state().categories[categoryId];
			if (!category) return;
			try {
				const response = await api.toggleCategoryActive(categoryId, !category.is_active);
				if (response.success) {
					await this.loadRootCategories();
					await this.loadAllCategories();
				}
			} catch (error) {
				logger.error('Failed to toggle category active state:', error);
			}
		},

		async handleToggleValueActive(valueId: string) {
			const s0 = state();
			const values = s0.selectedCategoryId ? s0.categoryValues[s0.selectedCategoryId] || [] : [];
			const value = values.find((v) => v.id === valueId);
			if (!value || !s0.selectedCategoryId) return;
			try {
				const response = await api.toggleValueActive(valueId, !value.is_active);
				if (response.success) {
					await this.loadCategoryValues(s0.selectedCategoryId);
				}
			} catch (error) {
				logger.error('Failed to toggle value active state:', error);
			}
		},

		// Multi-select for preview generation
		selectAllValues() {
			const s0 = state();
			const values = s0.selectedCategoryId ? s0.categoryValues[s0.selectedCategoryId] || [] : [];
			const allActiveValueIds = values.filter((v) => v.is_active).map((v) => v.id);
			update((s) => ({ ...s, selectedValueIds: new Set(allActiveValueIds) }));
		},

		deselectAllValues() {
			update((s) => ({ ...s, selectedValueIds: new Set() }));
		},

		toggleValueSelection(valueId: string) {
			update((s) => {
				const next = new Set(s.selectedValueIds);
				if (next.has(valueId)) {
					next.delete(valueId);
				} else {
					next.add(valueId);
				}
				return { ...s, selectedValueIds: next };
			});
		},

		selectValueIds(ids: string[]) {
			update((s) => ({ ...s, selectedValueIds: new Set(ids) }));
		},

		setCategoryForm(form: CategoryForm) {
			update((s) => ({ ...s, categoryForm: form }));
		},

		setValueForm(form: ValueForm) {
			update((s) => ({ ...s, valueForm: form }));
		},

		setSelectedCategoryId(id: string | null) {
			update((s) => ({ ...s, selectedCategoryId: id }));
		},

		setSelectedValueId(id: string | null) {
			update((s) => ({ ...s, selectedValueId: id }));
		},

		setEditMode(mode: EditMode) {
			update((s) => ({ ...s, editMode: mode }));
		},

		hasChildren(categoryId: string): boolean {
			const cat = state().categories[categoryId];
			return !!(cat?.children && cat.children.length > 0) || !cat?.childrenLoaded;
		},

		reset() {
			set(initialState);
		}
	};
}

export const phrasebookStore = createPhrasebookStore();

export const selectedCategory = derived(phrasebookStore, ($s) =>
	$s.selectedCategoryId ? $s.categories[$s.selectedCategoryId] : null
);

export const selectedCategoryValues = derived(phrasebookStore, ($s) =>
	$s.selectedCategoryId ? $s.categoryValues[$s.selectedCategoryId] || [] : []
);

export const selectedValue = derived(
	[phrasebookStore, selectedCategoryValues],
	([$s, $values]) => ($s.selectedValueId ? $values.find((v) => v.id === $s.selectedValueId) : null)
);

export const allActiveValueIds = derived(selectedCategoryValues, ($values) =>
	$values.filter((v) => v.is_active).map((v) => v.id)
);

export const selectedCount = derived(phrasebookStore, ($s) => $s.selectedValueIds.size);

export const activeCount = derived(allActiveValueIds, ($ids) => $ids.length);

export const isAllSelected = derived(
	[selectedCount, activeCount],
	([$selectedCount, $activeCount]) => $selectedCount === $activeCount && $activeCount > 0
);
