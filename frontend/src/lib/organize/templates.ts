import type { OrganizeCatalog, OrganizeScope, OrganizeSubject } from '$lib/types/organize';
import type { OrganizeStartRule } from './handoff';

export interface RuleTemplate {
	key: string;
	title: string;
	description: string;
	rule: OrganizeStartRule;
}

function addTo(name: string) {
	return [{ action: 'add_to_collection', config: { collection_name: name, create_if_missing: true } }];
}

const TEMPLATES: Record<OrganizeSubject, RuleTemplate[]> = {
	generation: [
		{
			key: 'gen-videos',
			title: 'Videos in one place',
			description: 'Add every new video to a Videos collection.',
			rule: {
				subject: 'generation',
				name: 'Videos',
				conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
				actions: addTo('Videos')
			}
		},
		{
			key: 'gen-portraits',
			title: 'Portraits',
			description: 'Sort tall images so avatars and phone wallpapers find each other.',
			rule: {
				subject: 'generation',
				name: 'Portraits',
				conditions: [{ fact: 'aspect', operator: 'is', value: 'portrait' }],
				actions: addTo('Portraits')
			}
		},
		{
			key: 'gen-landscapes',
			title: 'Landscapes',
			description: 'Sort wide images into one collection.',
			rule: {
				subject: 'generation',
				name: 'Landscapes',
				conditions: [{ fact: 'aspect', operator: 'is', value: 'landscape' }],
				actions: addTo('Landscapes')
			}
		},
		{
			key: 'gen-model',
			title: 'Everything from one model',
			description: 'Pick a model and file everything it makes together.',
			rule: {
				subject: 'generation',
				name: 'One model',
				conditions: [{ fact: 'model', operator: 'is', value: '' }],
				actions: []
			}
		}
	],
	upload: [
		{
			key: 'up-videos',
			title: 'Videos in one place',
			description: 'Add every uploaded video to a Videos collection.',
			rule: {
				subject: 'upload',
				name: 'Videos',
				conditions: [{ fact: 'media_kind', operator: 'is', value: 'video' }],
				actions: addTo('Videos')
			}
		},
		{
			key: 'up-audio',
			title: 'Audio in one place',
			description: 'Keep every uploaded sound together.',
			rule: {
				subject: 'upload',
				name: 'Audio',
				conditions: [{ fact: 'media_kind', operator: 'is', value: 'audio' }],
				actions: addTo('Audio')
			}
		},
		{
			key: 'up-portraits',
			title: 'Portraits',
			description: 'Sort tall uploads into one collection.',
			rule: {
				subject: 'upload',
				name: 'Portraits',
				conditions: [{ fact: 'aspect', operator: 'is', value: 'portrait' }],
				actions: addTo('Portraits')
			}
		}
	],
	model: [
		{
			key: 'model-loras',
			title: 'LoRAs together',
			description: 'File every new LoRA into one collection.',
			rule: {
				subject: 'model',
				name: 'LoRAs',
				conditions: [{ fact: 'model_type', operator: 'is', value: 'lora' }],
				actions: addTo('LoRAs')
			}
		},
		{
			key: 'model-sdxl',
			title: 'SDXL models',
			description: 'File every SDXL based model together.',
			rule: {
				subject: 'model',
				name: 'SDXL models',
				conditions: [{ fact: 'base_model', operator: 'is', value: 'sdxl' }],
				actions: addTo('SDXL')
			}
		}
	]
};

const INSTANCE_MODEL_TEMPLATES: RuleTemplate[] = [
	{
		key: 'model-group-by-name',
		title: 'Open a family to a user group',
		description: 'Models with a word in their name, such as krea2, go to the user group that should see them.',
		rule: {
			subject: 'model',
			name: 'Krea-2 models',
			conditions: [{ fact: 'name', operator: 'contains', value: 'krea2', ignore_separators: true }],
			actions: [{ action: 'assign_model_group', config: { group_id: null } }]
		}
	},
	{
		key: 'model-civitai',
		title: 'Fetch CivitAI data for new models',
		description: 'Checkpoints and LoRAs without marketplace data get their description, trigger words and previews.',
		rule: {
			subject: 'model',
			name: 'Fetch CivitAI data',
			conditions: [
				{ fact: 'source', operator: 'is_not', value: 'civitai' },
				{ fact: 'model_type', operator: 'is_any_of', value: ['checkpoint', 'lora'] }
			],
			actions: [{ action: 'civitai-provider.fetch_info', config: {} }]
		}
	}
];

export function templatesFor(
	subject: OrganizeSubject,
	catalog: OrganizeCatalog | null,
	scope: OrganizeScope = 'user'
): RuleTemplate[] {
	const all = scope === 'instance' ? (subject === 'model' ? INSTANCE_MODEL_TEMPLATES : []) : (TEMPLATES[subject] ?? []);
	if (!catalog) return scope === 'instance' ? [] : all;
	return all.filter(
		(template) =>
			template.rule.conditions.every((cond) =>
				catalog.facts.some((fact) => fact.key === cond.fact && fact.subjects.includes(subject))
			) &&
			(template.rule.actions ?? []).every((action) =>
				catalog.actions.some((spec) => spec.key === action.action && spec.subjects.includes(subject))
			)
	);
}
