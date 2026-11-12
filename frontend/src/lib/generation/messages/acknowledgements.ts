import { generationMessageRegistry } from '$lib/registries/generationMessageRegistry';

for (const type of ['subscribed', 'generation_update']) {
	generationMessageRegistry.register(type, { type, handle() {} });
}
