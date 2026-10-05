import type { OrganizeCatalog, OrganizeRule } from '../../src/lib/types/organize';

export const catalogFixture: OrganizeCatalog = {
	subjects: [
		{ key: 'generation', label: 'Generations', trigger: 'generation_completed', trigger_label: 'When a new generation completes', collection_scope: 'history', supports_tags: true },
		{ key: 'upload', label: 'Library uploads', trigger: 'upload_created', trigger_label: 'When I upload a file', collection_scope: 'library', supports_tags: true },
		{ key: 'model', label: 'Models', trigger: 'model_added', trigger_label: 'When a model is added', collection_scope: 'models', supports_tags: false }
	],
	kinds: {
		model_ref: { operators: ['is', 'is_any_of', 'is_not'] },
		enum: { operators: ['is', 'is_any_of', 'is_not'] },
		size: { operators: ['is', 'at_least', 'at_most'] },
		text: { operators: ['contains', 'not_contains'] },
		tag_list: { operators: ['has', 'has_not'] }
	},
	operators: {
		is: 'is',
		is_any_of: 'is any of',
		is_not: 'is not',
		at_least: 'is at least',
		at_most: 'is at most',
		contains: 'contains',
		not_contains: 'does not contain',
		has: 'has',
		has_not: 'does not have'
	},
	facts: [
		{ key: 'model', label: 'Model', subjects: ['generation'], kind: 'model_ref', operators: ['is', 'is_any_of', 'is_not'], picker: { model_types: ['checkpoint', 'diffusion_model', 'unet'] }, options: null, has_options_endpoint: false, description: '', source: 'core', component: null, previewable: true },
		{ key: 'media_kind', label: 'Media kind', subjects: ['generation', 'upload'], kind: 'enum', operators: ['is', 'is_any_of', 'is_not'], picker: { multi: true }, options: [{ value: 'image', label: 'Image' }, { value: 'video', label: 'Video' }, { value: 'audio', label: 'Audio' }], has_options_endpoint: false, description: '', source: 'core', component: null, previewable: true },
		{ key: 'preset', label: 'Preset', subjects: ['generation'], kind: 'enum', operators: ['is', 'is_any_of', 'is_not'], picker: { multi: true }, options: Array.from({ length: 8 }, (_, i) => ({ value: `p${i}`, label: `Preset ${i}` })), has_options_endpoint: false, description: '', source: 'core', component: null, previewable: true },
		{ key: 'resolution', label: 'Resolution', subjects: ['generation', 'upload'], kind: 'size', operators: ['is', 'at_least', 'at_most'], picker: { presets: [{ label: 'Square 1024', width: 1024, height: 1024 }, { label: 'Landscape', width: 1344, height: 768 }] }, options: null, has_options_endpoint: false, description: '', source: 'core', component: null, previewable: true },
		{ key: 'prompt', label: 'Prompt', subjects: ['generation'], kind: 'text', operators: ['contains', 'not_contains'], picker: { placeholder: 'a word' }, options: null, has_options_endpoint: false, description: '', source: 'core', component: null, previewable: true },
		{ key: 'tags', label: 'Tags', subjects: ['generation', 'upload', 'model'], kind: 'tag_list', operators: ['has', 'has_not'], picker: { tag_type: 'GENERATION' }, options: null, has_options_endpoint: true, description: '', source: 'core', component: null, previewable: true },
		{ key: 'tagger.mood', label: 'Mood', subjects: ['generation'], kind: 'sentiment_dial', operators: ['is'], picker: { placeholder: 'calm' }, options: null, has_options_endpoint: false, description: '', source: 'tagger', component: null, previewable: false }
	],
	actions: [
		{ key: 'add_to_collection', label: 'Add to collection', subjects: ['generation', 'upload', 'model'], config_schema: [{ key: 'collection_id', kind: 'collection', label: 'Collection', required: false }, { key: 'collection_name', kind: 'text', label: 'Name', required: false }, { key: 'parent_id', kind: 'collection', label: 'Create inside', required: false }, { key: 'create_if_missing', kind: 'bool', label: 'Create it if it goes missing', default: true }], requires_admin: false, undoable: true, source: 'core', component: null },
		{ key: 'add_tags', label: 'Add tags', subjects: ['generation', 'upload'], config_schema: [{ key: 'tags', kind: 'tag_list', label: 'Tags', required: true }], requires_admin: false, undoable: true, source: 'core', component: null },
		{ key: 'tagger.note', label: 'Leave a note', subjects: ['generation'], config_schema: [{ key: 'note', kind: 'text', label: 'Note', required: false }], requires_admin: false, undoable: false, source: 'tagger', component: null }
	]
};

export function ruleFixture(over: Partial<OrganizeRule> = {}): OrganizeRule {
	return {
		id: 'r1',
		name: 'Videos',
		subject: 'generation',
		trigger: 'generation_completed',
		match: 'all',
		conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
		actions: [{ action: 'add_to_collection', config: { collection_id: 'c1', collection_name: 'Videos', parent_id: null, create_if_missing: true } }],
		enabled: true,
		stop_after: false,
		position: 0,
		status: 'active',
		paused_reason: null,
		paused_at: null,
		issues: [],
		targets: { collections: [{ id: 'c1', name: 'Videos', exists: true }] },
		filed_count: 0,
		last_run_at: null,
		created_at: '2026-10-05T09:00:00+00:00',
		updated_at: '2026-10-05T09:00:00+00:00',
		...over
	};
}
