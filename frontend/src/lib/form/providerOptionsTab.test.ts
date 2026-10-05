import { describe, it, expect } from 'vitest';
import { applyCapabilitiesToSchema, type CloudCapabilities } from './capabilityBinder';
import { applyAudienceVisibilityToSchema } from '$lib/utils/audienceFilter';

function cloudForm() {
	return {
		properties: {
			root: {
				children: [
					{ type: 'model', name: 'model', audience: 'simple', configuration: { tasks: ['txt2img'] } },
					{
						type: 'tabs',
						audience: 'simple',
						children: [
							{
								type: 'tab',
								label: 'Generation',
								audience: 'simple',
								children: [{ type: 'textarea', name: 'prompt', audience: 'simple' }]
							},
							{
								type: 'tab',
								label: 'Provider options',
								audience: 'simple',
								children: [
									{
										type: 'accordion',
										audience: 'simple',
										children: [
											{
												type: 'cloud_options',
												name: 'provider_options',
												audience: 'simple',
												capability: { model_field: 'model' },
												configuration: { include_unbound: true }
											}
										]
									}
								]
							}
						]
					}
				]
			}
		}
	};
}

function caps(params: CloudCapabilities['params']): CloudCapabilities {
	return { model_id: 'm1', params, inputs: [] };
}

function providerTabVisible(capabilities: CloudCapabilities | null): boolean {
	const schema = cloudForm();
	applyCapabilitiesToSchema(schema as any, { model: capabilities });
	applyAudienceVisibilityToSchema(schema as any, 'simple');
	const tabs = schema.properties.root.children[1] as any;
	return tabs.children[1].visible !== false;
}

describe('Provider options tab in the Simple field view', () => {
	it('shows when the chosen model has extras of its own', () => {
		expect(providerTabVisible(caps([{ name: 'x.style', kind: 'enum', values: ['noir'], extra: true }]))).toBe(true);
	});

	it('stays out of the way when the model has nothing extra to set', () => {
		expect(providerTabVisible(caps([]))).toBe(false);
	});

	it('waits for the model capabilities before showing', () => {
		expect(providerTabVisible(null)).toBe(false);
	});
});
