import { test, expect, type Page } from '@playwright/test';
import { loginAsOwner, ownerToken, screenshot } from './helpers';

// The 3D viewer controls wave: wireframe toggle, camera presets, auto-rotate,
// exposure, screenshot, and the material inspector, all layered onto
// MeshPreview's <model-viewer>. Reuses workbench-mesh-display.spec.ts's
// upload -> restore -> workbench seeding path, but with a GLB that carries a
// real material + embedded texture so the inspector has something to show.
//
// Animation controls are deliberately NOT exercised here: a valid glTF
// animation needs real accessors/samplers/channels, and three.js's
// GLTFLoader (unlike this repo's own permissive inspector parser) rejects a
// malformed one outright - not worth the fixture complexity for this spec.
// `workbenchGallery.test.ts` and `MeshPreview`'s own logic already cover the
// "no animations -> no animation buttons" and "animations -> buttons appear"
// branches at the unit level.

const JOURNEY = 'workbench-mesh-controls';
const TABS_STORAGE_KEY = 'potionui_tabs_state';

test.use({
	launchOptions: {
		args: ['--disable-gpu', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader']
	}
});

// A real, decodable 1x1 transparent PNG (not just a header) - the browser's
// own <img> actually renders this as the inspector's texture swatch.
const TINY_PNG_BASE64 =
	'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=';

/** A minimal valid glTF-2.0 binary: one triangle with a material whose base
 * color texture is the embedded PNG above - enough for the material
 * inspector to have a real channel to render. */
function buildGlbWithMaterial(): Buffer {
	const positions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);
	const indices = new Uint16Array([0, 1, 2, 0]); // 4th entry pads to 4-byte alignment
	const geometryBin = Buffer.concat([Buffer.from(positions.buffer), Buffer.from(indices.buffer)]);
	const pngBytes = Buffer.from(TINY_PNG_BASE64, 'base64');

	const bin = Buffer.concat([geometryBin, pngBytes]);
	const imageByteOffset = geometryBin.length;

	const json = {
		asset: { version: '2.0' },
		scene: 0,
		scenes: [{ nodes: [0] }],
		nodes: [{ mesh: 0 }],
		meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }],
		materials: [
			{
				name: 'e2e_material',
				doubleSided: true,
				pbrMetallicRoughness: {
					baseColorFactor: [1, 1, 1, 1],
					baseColorTexture: { index: 0 },
					metallicFactor: 0.5,
					roughnessFactor: 0.5
				}
			}
		],
		textures: [{ source: 0 }],
		images: [{ bufferView: 2, mimeType: 'image/png' }],
		buffers: [{ byteLength: bin.length }],
		bufferViews: [
			{ buffer: 0, byteOffset: 0, byteLength: 36 },
			{ buffer: 0, byteOffset: 36, byteLength: 6 },
			{ buffer: 0, byteOffset: imageByteOffset, byteLength: pngBytes.length }
		],
		accessors: [
			{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] },
			{ bufferView: 1, componentType: 5123, count: 3, type: 'SCALAR' }
		]
	};
	let jsonStr = JSON.stringify(json);
	while (jsonStr.length % 4 !== 0) jsonStr += ' ';
	const jsonBuf = Buffer.from(jsonStr);

	const header = Buffer.alloc(12);
	header.writeUInt32LE(0x46546c67, 0);
	header.writeUInt32LE(2, 4);
	header.writeUInt32LE(12 + 8 + jsonBuf.length + 8 + bin.length, 8);

	const jsonChunk = Buffer.alloc(8);
	jsonChunk.writeUInt32LE(jsonBuf.length, 0);
	jsonChunk.writeUInt32LE(0x4e4f534a, 4);

	const binChunk = Buffer.alloc(8);
	binChunk.writeUInt32LE(bin.length, 0);
	binChunk.writeUInt32LE(0x004e4942, 4);

	return Buffer.concat([header, jsonChunk, jsonBuf, binChunk, bin]);
}

async function apiGet(page: Page, url: string, token: string) {
	const res = await page.request.get(url, { headers: { Authorization: `Bearer ${token}` } });
	expect(res.ok(), `GET ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

async function apiPost(page: Page, url: string, token: string, data?: unknown) {
	const res = await page.request.post(url, {
		headers: { Authorization: `Bearer ${token}` },
		data: data ?? {}
	});
	expect(res.ok(), `POST ${url} -> ${res.status()}`).toBeTruthy();
	return res.json();
}

test('mesh viewer controls: wireframe, camera presets, auto-rotate, exposure, screenshot, material inspector', async ({
	page
}) => {
	await loginAsOwner(page);
	const token = await ownerToken(page);

	const uploadRes = await page.request.post('/api/generations/upload', {
		headers: { Authorization: `Bearer ${token}` },
		multipart: {
			files: { name: 'workbench-mesh-controls.glb', mimeType: 'model/gltf-binary', buffer: buildGlbWithMaterial() }
		}
	});
	expect(uploadRes.ok(), `glb upload -> ${uploadRes.status()}: ${await uploadRes.text()}`).toBeTruthy();
	const uploadBody = await uploadRes.json();
	const generationId = uploadBody.data.generation_id as string;

	const me = await apiGet(page, '/api/auth/me', token);
	const list = await apiGet(page, '/api/presets?include_uninstalled=true', token);
	const preset = ((list.data || []) as Array<{ id: string; installed?: boolean }>)[0];
	if (!preset) {
		test.skip(true, 'No presets available on this throwaway instance.');
		return;
	}
	if (!preset.installed) {
		await apiPost(page, `/api/presets/${preset.id}/install`, token);
	}
	await apiPost(page, `/api/presets/${preset.id}/assign`, token, { user_ids: [me.data.id] });
	const modes = await apiGet(page, `/api/presets/${preset.id}/modes`, token);
	const modeName = modes.data?.modes?.[0]?.name as string | undefined;
	if (!modeName) {
		test.skip(true, `Preset ${preset.id} exposes no modes on this throwaway instance.`);
		return;
	}

	await page.addInitScript(
		({ key, genId, presetId, mode }) => {
			const raw = localStorage.getItem(key);
			if (!raw) return;
			const state = JSON.parse(raw);
			if (!state?.tabs?.length) return;
			state.tabs[0].activeGenerationId = genId;
			state.tabs[0].selectedPreset = presetId;
			state.tabs[0].selectedMode = mode;
			localStorage.setItem(key, JSON.stringify(state));
		},
		{ key: TABS_STORAGE_KEY, genId: generationId, presetId: preset.id, mode: modeName }
	);

	await page.reload();
	await page.waitForURL(/\/generate/, { timeout: 15000 });

	const viewer = page.locator('model-viewer');
	await expect(viewer, 'MeshPreview should mount a <model-viewer>').toBeVisible({ timeout: 30000 });
	await expect(viewer).toHaveCSS('opacity', '1', { timeout: 30000 });
	await expect(page.getByText('Loading model…')).toHaveCount(0);

	// --- Wireframe: toggling must not throw, and (when the deep-imported
	// $scene handle resolved) must actually flip a real THREE.Material flag.
	const wireframeButton = page.getByRole('button', { name: 'Toggle wireframe' });
	if (await wireframeButton.count()) {
		await wireframeButton.click();
		const wireframeApplied = await viewer.evaluate((el: any) => {
			let found = false;
			const scene = el[Object.getOwnPropertySymbols(el).find((s) => s.description === 'scene') as any];
			scene?.traverse?.((obj: any) => {
				const mats = Array.isArray(obj?.material) ? obj.material : obj?.material ? [obj.material] : [];
				for (const m of mats) if (m?.wireframe === true) found = true;
			});
			return found;
		});
		expect(wireframeApplied, 'wireframe toggle should set THREE.Material.wireframe on the loaded mesh').toBe(true);
		await wireframeButton.click(); // leave it off for the screenshot below
	} else {
		console.log(`[${JOURNEY}] wireframe button absent - $scene lookup did not resolve on this model-viewer build`);
	}

	// --- Camera presets: picking "Top" must change cameraOrbit.
	await page.getByRole('button', { name: 'Camera view' }).click();
	await page.getByRole('button', { name: 'Top', exact: true }).click();
	const orbitAfterTop = await viewer.evaluate((el: any) => el.cameraOrbit as string);
	expect(orbitAfterTop, 'selecting Top should set a 0deg polar cameraOrbit').toContain('0deg 0deg');

	// --- Auto-rotate toggle reflects on the element's real property.
	const autoRotateButton = page.getByRole('button', { name: 'Toggle auto-rotate' });
	await autoRotateButton.click();
	await expect.poll(() => viewer.evaluate((el: any) => el.autoRotate as boolean)).toBe(true);
	await autoRotateButton.click();
	await expect.poll(() => viewer.evaluate((el: any) => el.autoRotate as boolean)).toBe(false);

	// --- Exposure slider changes the element's exposure property.
	await page.getByRole('button', { name: 'Exposure' }).click();
	const slider = page.getByTestId('mesh-exposure-range');
	await slider.fill('2.5');
	await slider.dispatchEvent('input');
	await expect.poll(() => viewer.evaluate((el: any) => el.exposure as number)).toBeCloseTo(2.5, 1);

	// --- Screenshot triggers a real PNG download via model-viewer's toBlob().
	const [download] = await Promise.all([
		page.waitForEvent('download', { timeout: 10000 }),
		page.getByRole('button', { name: 'Screenshot' }).click()
	]);
	expect(download.suggestedFilename()).toMatch(/\.png$/);

	// --- Material inspector shows the embedded texture's real resolution.
	await page.getByRole('button', { name: 'Inspect materials' }).click();
	await expect(page.getByText('e2e_material')).toBeVisible({ timeout: 10000 });
	await expect(page.getByText('1×1')).toBeVisible();

	await screenshot(page, JOURNEY, '01-controls-and-inspector');
	console.log(`[${JOURNEY}] mesh viewer controls verified for generation ${generationId}`);
});
