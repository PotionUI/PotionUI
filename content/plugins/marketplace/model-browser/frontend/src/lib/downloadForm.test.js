import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeMe } from './capabilities.js';
import { installHostModelTypes } from './testModelTypes.js';
import {
	baseName,
	buildDownloadBody,
	canSubmit,
	cleanSubdir,
	downloadWarnings,
	extensionOf,
	filenameEdited,
	formProblems,
	initialForm,
	licenseFlags,
	sizeProblem,
	validateFilename,
	validateSubdir
} from './downloadForm.js';

const ADMIN = normalizeMe({
	user: { id: 'a', username: 'root', is_admin: true },
	capabilities: ['browse', 'request', 'download', 'approve'],
	params: { unrestricted: true }
});
const LIMITED = normalizeMe({
	user: { id: 'u', username: 'bob', is_admin: false },
	capabilities: ['browse', 'download'],
	params: { sources: ['civitai'], model_types: ['lora', 'embedding'], max_file_gb: 1, assign_to: 'group', allow_remote: false }
});

const DETAIL = { source: 'civitai', id: '58390', name: 'Detail Tweaker LoRA', mb_type: 'lora', gated: false, license: null };
const VERSION = { id: '62833', name: 'v1.0', early_access: false };
const FILE = { id: '1', name: 'add_detail.safetensors', size_bytes: 37_861_176, auth_provider_id: 'civitai' };
const HF_FILE = { id: 'unet/model.fp16.safetensors', name: 'unet/model.fp16.safetensors', size_bytes: 5 * 1024 ** 3 };

installHostModelTypes();

test('baseName and extensionOf', () => {
	assert.equal(baseName('unet/model.fp16.safetensors'), 'model.fp16.safetensors');
	assert.equal(baseName('a\\b.ckpt'), 'b.ckpt');
	assert.equal(baseName(null), '');
	assert.equal(extensionOf('unet/model.fp16.SafeTensors'), '.safetensors');
	assert.equal(extensionOf('README'), '');
	assert.equal(extensionOf('.hidden'), '');
});

test('validateSubdir follows the backend rules', () => {
	for (const ok of ['', '  ', 'styles', 'styles/anime', 'styles/anime/', 'My Loras']) {
		assert.equal(validateSubdir(ok), '', ok);
	}
	assert.match(validateSubdir('/abs'), /relative/);
	assert.match(validateSubdir('a/../b'), /'\.\.'/);
	assert.match(validateSubdir('..'), /'\.\.'/);
	assert.match(validateSubdir('a//b'), /empty/);
	assert.match(validateSubdir('./a'), /'\.'/);
	assert.match(validateSubdir('a\\b'), /backslash/);
	assert.match(validateSubdir('C:/x'), /colon/);
	assert.match(validateSubdir('a\u0001b'), /control/);
	assert.match(validateSubdir('x'.repeat(256)), /255/);
	assert.equal(cleanSubdir(' styles/anime// '), 'styles/anime');
});

test('validateFilename keeps a plain name and the extension', () => {
	assert.equal(validateFilename('detail.safetensors', 'add_detail.safetensors'), '');
	assert.equal(validateFilename('Detail.SAFETENSORS', 'add_detail.safetensors'), '');
	assert.match(validateFilename('detail.ckpt', 'add_detail.safetensors'), /Keep the \.safetensors extension/);
	assert.match(validateFilename('detail', 'add_detail.safetensors'), /\.safetensors/);
	assert.match(validateFilename('sub/detail.safetensors', 'add_detail.safetensors'), /plain file name/);
	assert.match(validateFilename('', 'add_detail.safetensors'), /Enter a file name/);
	assert.match(validateFilename('..', 'x'), /Enter a file name/);
	assert.equal(validateFilename('anything', 'README'), '');
	assert.match(validateFilename(`${'x'.repeat(250)}.safetensors`, 'a.safetensors'), /255/);
});

test('filenameEdited compares with the base name of the source file', () => {
	assert.equal(filenameEdited('model.fp16.safetensors', 'unet/model.fp16.safetensors'), false);
	assert.equal(filenameEdited(' model.fp16.safetensors ', 'unet/model.fp16.safetensors'), false);
	assert.equal(filenameEdited('renamed.safetensors', 'unet/model.fp16.safetensors'), true);
});

test('sizeProblem explains a file over the limit', () => {
	assert.equal(sizeProblem(LIMITED, 37_861_176), '');
	assert.equal(sizeProblem(LIMITED, 5 * 1024 ** 3), 'This file is 5.0 GB: your permissions allow files of at most 1 GB');
	assert.equal(sizeProblem(ADMIN, 5 * 1024 ** 3), '');
	assert.equal(sizeProblem(LIMITED, null), '');
});

test('sizeProblem with purpose request reads the request parameters', () => {
	const me = normalizeMe({
		user: { id: 'u1', is_admin: false },
		capabilities: ['browse', 'download', 'request'],
		params: { sources: ['civitai'], max_file_gb: 1 },
		request_params: { sources: ['civitai'], max_file_gb: 8 }
	});
	assert.equal(sizeProblem(me, 5 * 1024 ** 3), 'This file is 5.0 GB: your permissions allow files of at most 1 GB');
	assert.equal(sizeProblem(me, 5 * 1024 ** 3, { purpose: 'request' }), '');
	assert.equal(
		sizeProblem(me, 9 * 1024 ** 3, { purpose: 'request' }),
		'This file is 9.0 GB: your permissions let you ask for files of at most 8 GB'
	);
	assert.equal(sizeProblem(LIMITED, 5 * 1024 ** 3, { purpose: 'request' }), 'This file is 5.0 GB: your permissions let you ask for files of at most 1 GB');
});

test('initialForm starts on the suggested type when allowed', () => {
	assert.deepEqual(initialForm(LIMITED, DETAIL, FILE), {
		modelType: 'lora',
		subdir: '',
		filename: 'add_detail.safetensors',
		destination: '',
		group: ''
	});
	assert.equal(initialForm(LIMITED, { ...DETAIL, mb_type: 'checkpoint' }, FILE).modelType, '');
	assert.equal(initialForm(ADMIN, { ...DETAIL, mb_type: null }, HF_FILE).modelType, '');
	assert.equal(initialForm(ADMIN, DETAIL, HF_FILE).filename, 'model.fp16.safetensors');
});

test('formProblems checks every field', () => {
	const form = initialForm(LIMITED, DETAIL, FILE);
	assert.deepEqual(formProblems(form, { me: LIMITED, detail: DETAIL, file: FILE }), { modelType: '', subdir: '', filename: '', size: '' });
	assert.equal(canSubmit(formProblems(form, { me: LIMITED, detail: DETAIL, file: FILE })), true);

	const bad = { ...form, modelType: '', subdir: '../x', filename: 'x.bin' };
	const problems = formProblems(bad, { me: LIMITED, detail: DETAIL, file: { ...FILE, size_bytes: 2 * 1024 ** 3 } });
	assert.match(problems.modelType, /Choose/);
	assert.match(problems.subdir, /'\.\.'/);
	assert.match(problems.filename, /extension/);
	assert.match(problems.size, /at most 1 GB/);
	assert.equal(canSubmit(problems), false);

	assert.match(formProblems({ ...form, modelType: 'checkpoint' }, { me: LIMITED, detail: DETAIL, file: FILE }).modelType, /do not allow/);
});

test('buildDownloadBody sends only what was chosen', () => {
	const form = initialForm(ADMIN, DETAIL, FILE);
	assert.deepEqual(buildDownloadBody({ detail: DETAIL, version: VERSION, file: FILE, form }), {
		source: 'civitai',
		model_id: '58390',
		version_id: '62833',
		file_id: '1',
		model_type: 'lora'
	});
	const chosen = { ...form, subdir: ' styles/ ', filename: 'tweak.safetensors', destination: 'r1', group: 'g1' };
	assert.deepEqual(buildDownloadBody({ detail: DETAIL, version: VERSION, file: FILE, form: chosen }), {
		source: 'civitai',
		model_id: '58390',
		version_id: '62833',
		file_id: '1',
		model_type: 'lora',
		subdir: 'styles',
		filename: 'tweak.safetensors',
		destination_backend_id: 'r1',
		assign_group_id: 'g1'
	});
	const numericIds = buildDownloadBody({ detail: { ...DETAIL, id: 58390 }, version: { id: 62833 }, file: { ...FILE, id: 7 }, form });
	assert.equal(numericIds.model_id, '58390');
	assert.equal(numericIds.file_id, '7');
});

test('downloadWarnings flags early access, logins and non-commercial licences', () => {
	assert.deepEqual(downloadWarnings({ detail: DETAIL, version: VERSION, file: FILE }), []);
	const warnings = downloadWarnings({
		detail: { ...DETAIL, gated: true, license: { summary: 'x', commercial_use: [] } },
		version: { ...VERSION, early_access: true },
		file: FILE
	});
	assert.deepEqual(
		warnings.map((warning) => warning.title),
		['Early access', 'Needs a login', 'No commercial use']
	);
	assert.deepEqual(downloadWarnings({ detail: { ...DETAIL, gated: true }, version: VERSION, file: { ...FILE, auth_provider_id: null } }), []);
});

test('licenseFlags spells out the CivitAI flags', () => {
	assert.deepEqual(
		licenseFlags({ summary: 's', commercial_use: ['Image', 'Rent'], no_credit: false, derivatives: true, different_license: false }),
		['Commercial use: Image, Rent', 'Credit required', 'Derivatives allowed', 'Derivatives keep this licence']
	);
	assert.deepEqual(licenseFlags({ summary: 'MIT', commercial_use: null }), []);
	assert.deepEqual(licenseFlags({ commercial_use: [], no_credit: true, derivatives: false, different_license: true }), [
		'No commercial use',
		'No credit required',
		'No derivatives',
		'Derivatives may use another licence'
	]);
	assert.deepEqual(licenseFlags(null), []);
});

test("initialForm starts on the file's own type before the repo type", () => {
	const repo = { ...DETAIL, mb_type: 'upscaler' };
	assert.equal(initialForm(ADMIN, repo, { ...FILE, file_type: 'Text Encoder' }).modelType, 'text_encoder');
	assert.equal(initialForm(ADMIN, repo, { ...FILE, file_type: 'UNet' }).modelType, 'unet');
	assert.equal(initialForm(ADMIN, repo, { ...FILE, file_type: 'VAE' }).modelType, 'vae');
	assert.equal(initialForm(ADMIN, repo, { ...FILE, file_type: 'Model' }).modelType, 'upscaler');
	assert.equal(initialForm(ADMIN, repo, FILE).modelType, 'upscaler');
});
