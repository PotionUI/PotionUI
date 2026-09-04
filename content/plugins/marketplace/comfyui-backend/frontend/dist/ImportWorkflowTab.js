var __defProp = Object.defineProperty;
var __typeError = (msg) => {
  throw TypeError(msg);
};
var __defNormalProp = (obj, key2, value) => key2 in obj ? __defProp(obj, key2, { enumerable: true, configurable: true, writable: true, value }) : obj[key2] = value;
var __publicField = (obj, key2, value) => __defNormalProp(obj, typeof key2 !== "symbol" ? key2 + "" : key2, value);
var __accessCheck = (obj, member, msg) => member.has(obj) || __typeError("Cannot " + msg);
var __privateGet = (obj, member, getter) => (__accessCheck(obj, member, "read from private field"), getter ? getter.call(obj) : member.get(obj));
var __privateAdd = (obj, member, value) => member.has(obj) ? __typeError("Cannot add the same private member more than once") : member instanceof WeakSet ? member.add(obj) : member.set(obj, value);
var __privateSet = (obj, member, value, setter) => (__accessCheck(obj, member, "write to private field"), setter ? setter.call(obj, value) : member.set(obj, value), value);
var __privateMethod = (obj, member, method) => (__accessCheck(obj, member, "access private method"), method);

// content/plugins/node_modules/svelte/src/internal/client/constants.js
var DERIVED = 1 << 1;
var EFFECT = 1 << 2;
var RENDER_EFFECT = 1 << 3;
var MANAGED_EFFECT = 1 << 24;
var BLOCK_EFFECT = 1 << 4;
var BRANCH_EFFECT = 1 << 5;
var ROOT_EFFECT = 1 << 6;
var BOUNDARY_EFFECT = 1 << 7;
var CONNECTED = 1 << 9;
var CLEAN = 1 << 10;
var DIRTY = 1 << 11;
var MAYBE_DIRTY = 1 << 12;
var INERT = 1 << 13;
var DESTROYED = 1 << 14;
var REACTION_RAN = 1 << 15;
var DESTROYING = 1 << 25;
var EFFECT_TRANSPARENT = 1 << 16;
var EAGER_EFFECT = 1 << 17;
var HEAD_EFFECT = 1 << 18;
var EFFECT_PRESERVED = 1 << 19;
var USER_EFFECT = 1 << 20;
var EFFECT_OFFSCREEN = 1 << 25;
var WAS_MARKED = 1 << 16;
var REACTION_IS_UPDATING = 1 << 21;
var ASYNC = 1 << 22;
var ERROR_VALUE = 1 << 23;
var STATE_SYMBOL = /* @__PURE__ */ Symbol("$state");
var LEGACY_PROPS = /* @__PURE__ */ Symbol("legacy props");
var LOADING_ATTR_SYMBOL = /* @__PURE__ */ Symbol("");
var PROXY_PATH_SYMBOL = /* @__PURE__ */ Symbol("proxy path");
var ATTRIBUTES_CACHE = /* @__PURE__ */ Symbol("attributes");
var CLASS_CACHE = /* @__PURE__ */ Symbol("class");
var STYLE_CACHE = /* @__PURE__ */ Symbol("style");
var TEXT_CACHE = /* @__PURE__ */ Symbol("text");
var FORM_RESET_HANDLER = /* @__PURE__ */ Symbol("form reset");
var HMR_ANCHOR = /* @__PURE__ */ Symbol("hmr anchor");
var STALE_REACTION = new class StaleReactionError extends Error {
  constructor() {
    super(...arguments);
    __publicField(this, "name", "StaleReactionError");
    __publicField(this, "message", "The reaction that called `getAbortSignal()` was re-run or destroyed");
  }
}();
var IS_XHTML = (
  // We gotta write it like this because after downleveling the pure comment may end up in the wrong location
  !!globalThis.document?.contentType && /* @__PURE__ */ globalThis.document.contentType.includes("xml")
);
var TEXT_NODE = 3;
var COMMENT_NODE = 8;

// content/plugins/node_modules/esm-env/dev-fallback.js
var node_env = globalThis.process?.env?.NODE_ENV;
var dev_fallback_default = node_env && !node_env.toLowerCase().startsWith("prod");

// content/plugins/node_modules/svelte/src/internal/shared/utils.js
var is_array = Array.isArray;
var index_of = Array.prototype.indexOf;
var includes = Array.prototype.includes;
var array_from = Array.from;
var object_keys = Object.keys;
var define_property = Object.defineProperty;
var get_descriptor = Object.getOwnPropertyDescriptor;
var get_descriptors = Object.getOwnPropertyDescriptors;
var object_prototype = Object.prototype;
var array_prototype = Array.prototype;
var get_prototype_of = Object.getPrototypeOf;
var is_extensible = Object.isExtensible;
var noop = () => {
};
function run_all(arr) {
  for (var i = 0; i < arr.length; i++) {
    arr[i]();
  }
}
function deferred() {
  var resolve;
  var reject;
  var promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
function fallback(value, fallback2, lazy = false) {
  return value === void 0 ? lazy ? (
    /** @type {() => V} */
    fallback2()
  ) : (
    /** @type {V} */
    fallback2
  ) : value;
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/equality.js
function equals(value) {
  return value === this.v;
}
function safe_not_equal(a, b) {
  return a != a ? b == b : a !== b || a !== null && typeof a === "object" || typeof a === "function";
}
function safe_equals(value) {
  return !safe_not_equal(value, this.v);
}

// content/plugins/node_modules/svelte/src/internal/shared/errors.js
function invariant_violation(message) {
  if (dev_fallback_default) {
    const error = new Error(`invariant_violation
An invariant violation occurred, meaning Svelte's internal assumptions were flawed. This is a bug in Svelte, not your app \u2014 please open an issue at https://github.com/sveltejs/svelte, citing the following message: "${message}"
https://svelte.dev/e/invariant_violation`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/invariant_violation`);
  }
}
function lifecycle_outside_component(name) {
  if (dev_fallback_default) {
    const error = new Error(`lifecycle_outside_component
\`${name}(...)\` can only be used during component initialisation
https://svelte.dev/e/lifecycle_outside_component`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/lifecycle_outside_component`);
  }
}

// content/plugins/node_modules/svelte/src/internal/client/errors.js
function async_derived_orphan() {
  if (dev_fallback_default) {
    const error = new Error(`async_derived_orphan
Cannot create a \`$derived(...)\` with an \`await\` expression outside of an effect tree
https://svelte.dev/e/async_derived_orphan`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/async_derived_orphan`);
  }
}
function bind_invalid_checkbox_value() {
  if (dev_fallback_default) {
    const error = new Error(`bind_invalid_checkbox_value
Using \`bind:value\` together with a checkbox input is not allowed. Use \`bind:checked\` instead
https://svelte.dev/e/bind_invalid_checkbox_value`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/bind_invalid_checkbox_value`);
  }
}
function derived_references_self() {
  if (dev_fallback_default) {
    const error = new Error(`derived_references_self
A derived value cannot reference itself recursively
https://svelte.dev/e/derived_references_self`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/derived_references_self`);
  }
}
function each_key_duplicate(a, b, value) {
  if (dev_fallback_default) {
    const error = new Error(`each_key_duplicate
${value ? `Keyed each block has duplicate key \`${value}\` at indexes ${a} and ${b}` : `Keyed each block has duplicate key at indexes ${a} and ${b}`}
https://svelte.dev/e/each_key_duplicate`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/each_key_duplicate`);
  }
}
function each_key_volatile(index2, a, b) {
  if (dev_fallback_default) {
    const error = new Error(`each_key_volatile
Keyed each block has key that is not idempotent \u2014 the key for item at index ${index2} was \`${a}\` but is now \`${b}\`. Keys must be the same each time for a given item
https://svelte.dev/e/each_key_volatile`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/each_key_volatile`);
  }
}
function effect_in_teardown(rune) {
  if (dev_fallback_default) {
    const error = new Error(`effect_in_teardown
\`${rune}\` cannot be used inside an effect cleanup function
https://svelte.dev/e/effect_in_teardown`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/effect_in_teardown`);
  }
}
function effect_in_unowned_derived() {
  if (dev_fallback_default) {
    const error = new Error(`effect_in_unowned_derived
Effect cannot be created inside a \`$derived\` value that was not itself created inside an effect
https://svelte.dev/e/effect_in_unowned_derived`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/effect_in_unowned_derived`);
  }
}
function effect_orphan(rune) {
  if (dev_fallback_default) {
    const error = new Error(`effect_orphan
\`${rune}\` can only be used inside an effect (e.g. during component initialisation)
https://svelte.dev/e/effect_orphan`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/effect_orphan`);
  }
}
function effect_update_depth_exceeded() {
  if (dev_fallback_default) {
    const error = new Error(`effect_update_depth_exceeded
Maximum update depth exceeded. This typically indicates that an effect reads and writes the same piece of state
https://svelte.dev/e/effect_update_depth_exceeded`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/effect_update_depth_exceeded`);
  }
}
function hydration_failed() {
  if (dev_fallback_default) {
    const error = new Error(`hydration_failed
Failed to hydrate the application
https://svelte.dev/e/hydration_failed`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/hydration_failed`);
  }
}
function props_invalid_value(key2) {
  if (dev_fallback_default) {
    const error = new Error(`props_invalid_value
Cannot do \`bind:${key2}={undefined}\` when \`${key2}\` has a fallback value
https://svelte.dev/e/props_invalid_value`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/props_invalid_value`);
  }
}
function rune_outside_svelte(rune) {
  if (dev_fallback_default) {
    const error = new Error(`rune_outside_svelte
The \`${rune}\` rune is only available inside \`.svelte\` and \`.svelte.js/ts\` files
https://svelte.dev/e/rune_outside_svelte`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/rune_outside_svelte`);
  }
}
function state_descriptors_fixed() {
  if (dev_fallback_default) {
    const error = new Error(`state_descriptors_fixed
Property descriptors defined on \`$state\` objects must contain \`value\` and always be \`enumerable\`, \`configurable\` and \`writable\`.
https://svelte.dev/e/state_descriptors_fixed`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/state_descriptors_fixed`);
  }
}
function state_prototype_fixed() {
  if (dev_fallback_default) {
    const error = new Error(`state_prototype_fixed
Cannot set prototype of \`$state\` object
https://svelte.dev/e/state_prototype_fixed`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/state_prototype_fixed`);
  }
}
function state_unsafe_mutation() {
  if (dev_fallback_default) {
    const error = new Error(`state_unsafe_mutation
Updating state inside \`$derived(...)\`, \`$inspect(...)\` or a template expression is forbidden. If the value should not be reactive, declare it without \`$state\`
https://svelte.dev/e/state_unsafe_mutation`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/state_unsafe_mutation`);
  }
}
function svelte_boundary_reset_onerror() {
  if (dev_fallback_default) {
    const error = new Error(`svelte_boundary_reset_onerror
A \`<svelte:boundary>\` \`reset\` function cannot be called while an error is still being handled
https://svelte.dev/e/svelte_boundary_reset_onerror`);
    error.name = "Svelte error";
    throw error;
  } else {
    throw new Error(`https://svelte.dev/e/svelte_boundary_reset_onerror`);
  }
}

// content/plugins/node_modules/svelte/src/internal/flags/index.js
var async_mode_flag = false;
var legacy_mode_flag = false;
var tracing_mode_flag = false;

// content/plugins/node_modules/svelte/src/constants.js
var EACH_ITEM_REACTIVE = 1;
var EACH_INDEX_REACTIVE = 1 << 1;
var EACH_IS_CONTROLLED = 1 << 2;
var EACH_IS_ANIMATED = 1 << 3;
var EACH_ITEM_IMMUTABLE = 1 << 4;
var PROPS_IS_IMMUTABLE = 1;
var PROPS_IS_RUNES = 1 << 1;
var PROPS_IS_UPDATED = 1 << 2;
var PROPS_IS_BINDABLE = 1 << 3;
var PROPS_IS_LAZY_INITIAL = 1 << 4;
var TRANSITION_OUT = 1 << 1;
var TRANSITION_GLOBAL = 1 << 2;
var TEMPLATE_FRAGMENT = 1;
var TEMPLATE_USE_IMPORT_NODE = 1 << 1;
var TEMPLATE_USE_SVG = 1 << 2;
var TEMPLATE_USE_MATHML = 1 << 3;
var HYDRATION_START = "[";
var HYDRATION_START_ELSE = "[!";
var HYDRATION_START_FAILED = "[?";
var HYDRATION_END = "]";
var HYDRATION_ERROR = {};
var ELEMENT_PRESERVE_ATTRIBUTE_CASE = 1 << 1;
var ELEMENT_IS_INPUT = 1 << 2;
var UNINITIALIZED = /* @__PURE__ */ Symbol("uninitialized");
var FILENAME = /* @__PURE__ */ Symbol("filename");
var NAMESPACE_HTML = "http://www.w3.org/1999/xhtml";

// content/plugins/node_modules/svelte/src/internal/client/dev/tracing.js
var tracing_expressions = null;
function tag(source2, label) {
  source2.label = label;
  tag_proxy(source2.v, label);
  return source2;
}
function tag_proxy(value, label) {
  value?.[PROXY_PATH_SYMBOL]?.(label);
  return value;
}

// content/plugins/node_modules/svelte/src/internal/shared/dev.js
function get_error(label) {
  const error = new Error();
  const stack2 = get_stack();
  if (stack2.length === 0) {
    return null;
  }
  stack2.unshift("\n");
  define_property(error, "stack", {
    value: stack2.join("\n")
  });
  define_property(error, "name", {
    value: label
  });
  return (
    /** @type {Error & { stack: string }} */
    error
  );
}
function get_stack() {
  const limit = Error.stackTraceLimit;
  Error.stackTraceLimit = Infinity;
  const stack2 = new Error().stack;
  Error.stackTraceLimit = limit;
  if (!stack2) return [];
  const lines = stack2.split("\n");
  const new_lines = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const posixified = line.replaceAll("\\", "/");
    if (line.trim() === "Error") {
      continue;
    }
    if (line.includes("validate_each_keys")) {
      return [];
    }
    if (posixified.includes("svelte/src/internal") || posixified.includes("node_modules/.vite")) {
      continue;
    }
    new_lines.push(line);
  }
  return new_lines;
}
function invariant(condition, message) {
  if (!dev_fallback_default) {
    throw new Error("invariant(...) was not guarded by if (DEV)");
  }
  if (!condition) invariant_violation(message);
}

// content/plugins/node_modules/svelte/src/internal/client/context.js
var component_context = null;
function set_component_context(context) {
  component_context = context;
}
var dev_stack = null;
function set_dev_stack(stack2) {
  dev_stack = stack2;
}
var dev_current_component_function = null;
function set_dev_current_component_function(fn) {
  dev_current_component_function = fn;
}
function push(props, runes = false, fn) {
  component_context = {
    p: component_context,
    i: false,
    c: null,
    e: null,
    s: props,
    x: null,
    r: (
      /** @type {Effect} */
      active_effect
    ),
    l: legacy_mode_flag && !runes ? { s: null, u: null, $: [] } : null
  };
  if (dev_fallback_default) {
    component_context.function = fn;
    dev_current_component_function = fn;
  }
}
function pop(component2) {
  var context = (
    /** @type {ComponentContext} */
    component_context
  );
  var effects = context.e;
  if (effects !== null) {
    context.e = null;
    for (var fn of effects) {
      create_user_effect(fn);
    }
  }
  if (component2 !== void 0) {
    context.x = component2;
  }
  context.i = true;
  component_context = context.p;
  if (dev_fallback_default) {
    dev_current_component_function = component_context?.function ?? null;
  }
  return component2 ?? /** @type {T} */
  {};
}
function is_runes() {
  return !legacy_mode_flag || component_context !== null && component_context.l === null;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/task.js
var micro_tasks = [];
function run_micro_tasks() {
  var tasks = micro_tasks;
  micro_tasks = [];
  run_all(tasks);
}
function queue_micro_task(fn) {
  if (micro_tasks.length === 0 && !is_flushing_sync) {
    var tasks = micro_tasks;
    queueMicrotask(() => {
      if (tasks === micro_tasks) run_micro_tasks();
    });
  }
  micro_tasks.push(fn);
}
function flush_tasks() {
  while (micro_tasks.length > 0) {
    run_micro_tasks();
  }
}

// content/plugins/node_modules/svelte/src/internal/client/warnings.js
var bold = "font-weight: bold";
var normal = "font-weight: normal";
function await_reactivity_loss(name) {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] await_reactivity_loss
%cDetected reactivity loss when reading \`${name}\`. This happens when state is read in an async function after an earlier \`await\`
https://svelte.dev/e/await_reactivity_loss`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/await_reactivity_loss`);
  }
}
function await_waterfall(name, location) {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] await_waterfall
%cAn async derived, \`${name}\` (${location}) was not read immediately after it resolved. This often indicates an unnecessary waterfall, which can slow down your app
https://svelte.dev/e/await_waterfall`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/await_waterfall`);
  }
}
function derived_inert() {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] derived_inert
%cReading a derived belonging to a now-destroyed effect may result in stale values
https://svelte.dev/e/derived_inert`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/derived_inert`);
  }
}
function hydration_attribute_changed(attribute, html2, value) {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] hydration_attribute_changed
%cThe \`${attribute}\` attribute on \`${html2}\` changed its value between server and client renders. The client value, \`${value}\`, will be ignored in favour of the server value
https://svelte.dev/e/hydration_attribute_changed`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/hydration_attribute_changed`);
  }
}
function hydration_mismatch(location) {
  if (dev_fallback_default) {
    console.warn(
      `%c[svelte] hydration_mismatch
%c${location ? `Hydration failed because the initial UI does not match what was rendered on the server. The error occurred near ${location}` : "Hydration failed because the initial UI does not match what was rendered on the server"}
https://svelte.dev/e/hydration_mismatch`,
      bold,
      normal
    );
  } else {
    console.warn(`https://svelte.dev/e/hydration_mismatch`);
  }
}
function lifecycle_double_unmount() {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] lifecycle_double_unmount
%cTried to unmount a component that was not mounted
https://svelte.dev/e/lifecycle_double_unmount`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/lifecycle_double_unmount`);
  }
}
function select_multiple_invalid_value() {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] select_multiple_invalid_value
%cThe \`value\` property of a \`<select multiple>\` element should be an array, but it received a non-array value. The selection will be kept as is.
https://svelte.dev/e/select_multiple_invalid_value`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/select_multiple_invalid_value`);
  }
}
function state_proxy_equality_mismatch(operator) {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] state_proxy_equality_mismatch
%cReactive \`$state(...)\` proxies and the values they proxy have different identities. Because of this, comparisons with \`${operator}\` will produce unexpected results
https://svelte.dev/e/state_proxy_equality_mismatch`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/state_proxy_equality_mismatch`);
  }
}
function state_proxy_unmount() {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] state_proxy_unmount
%cTried to unmount a state proxy, rather than a component
https://svelte.dev/e/state_proxy_unmount`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/state_proxy_unmount`);
  }
}
function svelte_boundary_reset_noop() {
  if (dev_fallback_default) {
    console.warn(`%c[svelte] svelte_boundary_reset_noop
%cA \`<svelte:boundary>\` \`reset\` function only resets the boundary the first time it is called
https://svelte.dev/e/svelte_boundary_reset_noop`, bold, normal);
  } else {
    console.warn(`https://svelte.dev/e/svelte_boundary_reset_noop`);
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/hydration.js
var hydrating = false;
function set_hydrating(value) {
  hydrating = value;
}
var hydrate_node;
function set_hydrate_node(node) {
  if (node === null) {
    hydration_mismatch();
    throw HYDRATION_ERROR;
  }
  return hydrate_node = node;
}
function hydrate_next() {
  return set_hydrate_node(get_next_sibling(hydrate_node));
}
function reset(node) {
  if (!hydrating) return;
  if (get_next_sibling(hydrate_node) !== null) {
    hydration_mismatch();
    throw HYDRATION_ERROR;
  }
  hydrate_node = node;
}
function next(count = 1) {
  if (hydrating) {
    var i = count;
    var node = hydrate_node;
    while (i--) {
      node = /** @type {TemplateNode} */
      get_next_sibling(node);
    }
    hydrate_node = node;
  }
}
function skip_nodes(remove = true) {
  var depth = 0;
  var node = hydrate_node;
  while (true) {
    if (node.nodeType === COMMENT_NODE) {
      var data = (
        /** @type {Comment} */
        node.data
      );
      if (data === HYDRATION_END) {
        if (depth === 0) return node;
        depth -= 1;
      } else if (data === HYDRATION_START || data === HYDRATION_START_ELSE || // "[1", "[2", etc. for if blocks
      data[0] === "[" && !isNaN(Number(data.slice(1)))) {
        depth += 1;
      }
    }
    var next2 = (
      /** @type {TemplateNode} */
      get_next_sibling(node)
    );
    if (remove) node.remove();
    node = next2;
  }
}
function read_hydration_instruction(node) {
  if (!node || node.nodeType !== COMMENT_NODE) {
    hydration_mismatch();
    throw HYDRATION_ERROR;
  }
  return (
    /** @type {Comment} */
    node.data
  );
}

// content/plugins/node_modules/svelte/src/internal/client/proxy.js
var regex_is_valid_identifier = /^[a-zA-Z_$][a-zA-Z_$0-9]*$/;
function proxy(value) {
  if (typeof value !== "object" || value === null || STATE_SYMBOL in value) {
    return value;
  }
  const prototype = get_prototype_of(value);
  if (prototype !== object_prototype && prototype !== array_prototype) {
    return value;
  }
  var sources = /* @__PURE__ */ new Map();
  var is_proxied_array = is_array(value);
  var version = state(0);
  var stack2 = dev_fallback_default && tracing_mode_flag ? get_error("created at") : null;
  var parent_version = update_version;
  var with_parent = (fn) => {
    if (update_version === parent_version) {
      return fn();
    }
    var reaction = active_reaction;
    var version2 = update_version;
    set_active_reaction(null);
    set_update_version(parent_version);
    var result = fn();
    set_active_reaction(reaction);
    set_update_version(version2);
    return result;
  };
  if (is_proxied_array) {
    sources.set("length", state(
      /** @type {any[]} */
      value.length,
      stack2
    ));
    if (dev_fallback_default) {
      value = /** @type {any} */
      inspectable_array(
        /** @type {any[]} */
        value
      );
    }
  }
  var path = "";
  let updating = false;
  function update_path(new_path) {
    if (updating) return;
    updating = true;
    path = new_path;
    tag(version, `${path} version`);
    for (const [prop2, source2] of sources) {
      tag(source2, get_label(path, prop2));
    }
    updating = false;
  }
  return new Proxy(
    /** @type {any} */
    value,
    {
      defineProperty(_, prop2, descriptor) {
        if (!("value" in descriptor) || descriptor.configurable === false || descriptor.enumerable === false || descriptor.writable === false) {
          state_descriptors_fixed();
        }
        var s = sources.get(prop2);
        if (s === void 0) {
          with_parent(() => {
            var s2 = state(descriptor.value, stack2);
            sources.set(prop2, s2);
            if (dev_fallback_default && typeof prop2 === "string") {
              tag(s2, get_label(path, prop2));
            }
            return s2;
          });
        } else {
          set(s, descriptor.value, true);
        }
        return true;
      },
      deleteProperty(target, prop2) {
        var s = sources.get(prop2);
        if (s === void 0) {
          if (prop2 in target) {
            const s2 = with_parent(() => state(UNINITIALIZED, stack2));
            sources.set(prop2, s2);
            increment(version);
            if (dev_fallback_default) {
              tag(s2, get_label(path, prop2));
            }
          }
        } else {
          set(s, UNINITIALIZED);
          increment(version);
        }
        return true;
      },
      get(target, prop2, receiver) {
        if (prop2 === STATE_SYMBOL) {
          return value;
        }
        if (dev_fallback_default && prop2 === PROXY_PATH_SYMBOL) {
          return update_path;
        }
        var s = sources.get(prop2);
        var exists = prop2 in target;
        if (s === void 0 && (!exists || get_descriptor(target, prop2)?.writable)) {
          s = with_parent(() => {
            var p = proxy(exists ? target[prop2] : UNINITIALIZED);
            var s2 = state(p, stack2);
            if (dev_fallback_default) {
              tag(s2, get_label(path, prop2));
            }
            return s2;
          });
          sources.set(prop2, s);
        }
        if (s !== void 0) {
          var v = get(s);
          return v === UNINITIALIZED ? void 0 : v;
        }
        return Reflect.get(target, prop2, receiver);
      },
      getOwnPropertyDescriptor(target, prop2) {
        var descriptor = Reflect.getOwnPropertyDescriptor(target, prop2);
        if (descriptor && "value" in descriptor) {
          var s = sources.get(prop2);
          if (s) descriptor.value = get(s);
        } else if (descriptor === void 0) {
          var source2 = sources.get(prop2);
          var value2 = source2?.v;
          if (source2 !== void 0 && value2 !== UNINITIALIZED) {
            return {
              enumerable: true,
              configurable: true,
              value: value2,
              writable: true
            };
          }
        }
        return descriptor;
      },
      has(target, prop2) {
        if (prop2 === STATE_SYMBOL) {
          return true;
        }
        var s = sources.get(prop2);
        var has = s !== void 0 && s.v !== UNINITIALIZED || Reflect.has(target, prop2);
        if (s !== void 0 || active_effect !== null && (!has || get_descriptor(target, prop2)?.writable)) {
          if (s === void 0) {
            s = with_parent(() => {
              var p = has ? proxy(target[prop2]) : UNINITIALIZED;
              var s2 = state(p, stack2);
              if (dev_fallback_default) {
                tag(s2, get_label(path, prop2));
              }
              return s2;
            });
            sources.set(prop2, s);
          }
          var value2 = get(s);
          if (value2 === UNINITIALIZED) {
            return false;
          }
        }
        return has;
      },
      set(target, prop2, value2, receiver) {
        var s = sources.get(prop2);
        var has = prop2 in target;
        if (is_proxied_array && prop2 === "length") {
          for (var i = value2; i < /** @type {Source<number>} */
          s.v; i += 1) {
            var other_s = sources.get(i + "");
            if (other_s !== void 0) {
              set(other_s, UNINITIALIZED);
            } else if (i in target) {
              other_s = with_parent(() => state(UNINITIALIZED, stack2));
              sources.set(i + "", other_s);
              if (dev_fallback_default) {
                tag(other_s, get_label(path, i));
              }
            }
          }
        }
        if (s === void 0) {
          if (!has || get_descriptor(target, prop2)?.writable) {
            s = with_parent(() => state(void 0, stack2));
            if (dev_fallback_default) {
              tag(s, get_label(path, prop2));
            }
            set(s, proxy(value2));
            sources.set(prop2, s);
          }
        } else {
          has = s.v !== UNINITIALIZED;
          var p = with_parent(() => proxy(value2));
          set(s, p);
        }
        var descriptor = Reflect.getOwnPropertyDescriptor(target, prop2);
        if (descriptor?.set) {
          descriptor.set.call(receiver, value2);
        }
        if (!has) {
          if (is_proxied_array && typeof prop2 === "string") {
            var ls = (
              /** @type {Source<number>} */
              sources.get("length")
            );
            var n = Number(prop2);
            if (Number.isInteger(n) && n >= ls.v) {
              set(ls, n + 1);
            }
          }
          increment(version);
        }
        return true;
      },
      ownKeys(target) {
        get(version);
        var own_keys = Reflect.ownKeys(target).filter((key3) => {
          var source3 = sources.get(key3);
          return source3 === void 0 || source3.v !== UNINITIALIZED;
        });
        for (var [key2, source2] of sources) {
          if (source2.v !== UNINITIALIZED && !(key2 in target)) {
            own_keys.push(key2);
          }
        }
        return own_keys;
      },
      setPrototypeOf() {
        state_prototype_fixed();
      }
    }
  );
}
function get_label(path, prop2) {
  if (typeof prop2 === "symbol") return `${path}[Symbol(${prop2.description ?? ""})]`;
  if (regex_is_valid_identifier.test(prop2)) return `${path}.${prop2}`;
  return /^\d+$/.test(prop2) ? `${path}[${prop2}]` : `${path}['${prop2}']`;
}
function get_proxied_value(value) {
  try {
    if (value !== null && typeof value === "object" && STATE_SYMBOL in value) {
      return value[STATE_SYMBOL];
    }
  } catch {
  }
  return value;
}
function is(a, b) {
  return Object.is(get_proxied_value(a), get_proxied_value(b));
}
var ARRAY_MUTATING_METHODS = /* @__PURE__ */ new Set([
  "copyWithin",
  "fill",
  "pop",
  "push",
  "reverse",
  "shift",
  "sort",
  "splice",
  "unshift"
]);
function inspectable_array(array) {
  return new Proxy(array, {
    get(target, prop2, receiver) {
      var value = Reflect.get(target, prop2, receiver);
      if (!ARRAY_MUTATING_METHODS.has(
        /** @type {string} */
        prop2
      )) {
        return value;
      }
      return function(...args) {
        set_eager_effects_deferred();
        var result = value.apply(this, args);
        flush_eager_effects();
        return result;
      };
    }
  });
}

// content/plugins/node_modules/svelte/src/internal/client/dev/equality.js
function init_array_prototype_warnings() {
  const array_prototype2 = Array.prototype;
  const cleanup = Array.__svelte_cleanup;
  if (cleanup) {
    cleanup();
  }
  const { indexOf, lastIndexOf, includes: includes2 } = array_prototype2;
  array_prototype2.indexOf = function(item, from_index) {
    const index2 = indexOf.call(this, item, from_index);
    if (index2 === -1) {
      for (let i = from_index ?? 0; i < this.length; i += 1) {
        if (get_proxied_value(this[i]) === item) {
          state_proxy_equality_mismatch("array.indexOf(...)");
          break;
        }
      }
    }
    return index2;
  };
  array_prototype2.lastIndexOf = function(item, from_index) {
    const index2 = lastIndexOf.call(this, item, from_index ?? this.length - 1);
    if (index2 === -1) {
      for (let i = 0; i <= (from_index ?? this.length - 1); i += 1) {
        if (get_proxied_value(this[i]) === item) {
          state_proxy_equality_mismatch("array.lastIndexOf(...)");
          break;
        }
      }
    }
    return index2;
  };
  array_prototype2.includes = function(item, from_index) {
    const has = includes2.call(this, item, from_index);
    if (!has) {
      for (let i = 0; i < this.length; i += 1) {
        if (get_proxied_value(this[i]) === item) {
          state_proxy_equality_mismatch("array.includes(...)");
          break;
        }
      }
    }
    return has;
  };
  Array.__svelte_cleanup = () => {
    array_prototype2.indexOf = indexOf;
    array_prototype2.lastIndexOf = lastIndexOf;
    array_prototype2.includes = includes2;
  };
}

// content/plugins/node_modules/svelte/src/internal/client/dom/operations.js
var $window;
var $document;
var is_firefox;
var first_child_getter;
var next_sibling_getter;
function init_operations() {
  if ($window !== void 0) {
    return;
  }
  $window = window;
  $document = document;
  is_firefox = /Firefox/.test(navigator.userAgent);
  var element_prototype = Element.prototype;
  var node_prototype = Node.prototype;
  var text_prototype = Text.prototype;
  first_child_getter = get_descriptor(node_prototype, "firstChild").get;
  next_sibling_getter = get_descriptor(node_prototype, "nextSibling").get;
  if (is_extensible(element_prototype)) {
    element_prototype[CLASS_CACHE] = void 0;
    element_prototype[ATTRIBUTES_CACHE] = null;
    element_prototype[STYLE_CACHE] = void 0;
    element_prototype.__e = void 0;
  }
  if (is_extensible(text_prototype)) {
    text_prototype[TEXT_CACHE] = void 0;
  }
  if (dev_fallback_default) {
    element_prototype.__svelte_meta = null;
    init_array_prototype_warnings();
  }
}
function create_text(value = "") {
  return document.createTextNode(value);
}
// @__NO_SIDE_EFFECTS__
function get_first_child(node) {
  return (
    /** @type {TemplateNode | null} */
    first_child_getter.call(node)
  );
}
// @__NO_SIDE_EFFECTS__
function get_next_sibling(node) {
  return (
    /** @type {TemplateNode | null} */
    next_sibling_getter.call(node)
  );
}
function child(node, is_text) {
  if (!hydrating) {
    return /* @__PURE__ */ get_first_child(node);
  }
  var child2 = /* @__PURE__ */ get_first_child(hydrate_node);
  if (child2 === null) {
    child2 = hydrate_node.appendChild(create_text());
  } else if (is_text && child2.nodeType !== TEXT_NODE) {
    var text2 = create_text();
    child2?.before(text2);
    set_hydrate_node(text2);
    return text2;
  }
  if (is_text) {
    merge_text_nodes(
      /** @type {Text} */
      child2
    );
  }
  set_hydrate_node(child2);
  return child2;
}
function first_child(node, is_text = false) {
  if (!hydrating) {
    var first = /* @__PURE__ */ get_first_child(node);
    if (first instanceof Comment && first.data === "") return /* @__PURE__ */ get_next_sibling(first);
    return first;
  }
  if (is_text) {
    if (hydrate_node?.nodeType !== TEXT_NODE) {
      var text2 = create_text();
      hydrate_node?.before(text2);
      set_hydrate_node(text2);
      return text2;
    }
    merge_text_nodes(
      /** @type {Text} */
      hydrate_node
    );
  }
  return hydrate_node;
}
function sibling(node, count = 1, is_text = false) {
  let next_sibling = hydrating ? hydrate_node : node;
  var last_sibling;
  while (count--) {
    last_sibling = next_sibling;
    next_sibling = /** @type {TemplateNode} */
    /* @__PURE__ */ get_next_sibling(next_sibling);
  }
  if (!hydrating) {
    return next_sibling;
  }
  if (is_text) {
    if (next_sibling?.nodeType !== TEXT_NODE) {
      var text2 = create_text();
      if (next_sibling === null) {
        last_sibling?.after(text2);
      } else {
        next_sibling.before(text2);
      }
      set_hydrate_node(text2);
      return text2;
    }
    merge_text_nodes(
      /** @type {Text} */
      next_sibling
    );
  }
  set_hydrate_node(next_sibling);
  return next_sibling;
}
function clear_text_content(node) {
  node.textContent = "";
}
function should_defer_append() {
  if (!async_mode_flag) return false;
  if (eager_block_effects !== null) return false;
  var flags2 = (
    /** @type {Effect} */
    active_effect.f
  );
  return (flags2 & REACTION_RAN) !== 0;
}
function create_element(tag2, namespace, is2) {
  if (namespace == null || namespace === NAMESPACE_HTML) {
    return (
      /** @type {T extends keyof HTMLElementTagNameMap ? HTMLElementTagNameMap[T] : Element} */
      is2 ? document.createElement(tag2, { is: is2 }) : document.createElement(tag2)
    );
  }
  return (
    /** @type {T extends keyof HTMLElementTagNameMap ? HTMLElementTagNameMap[T] : Element} */
    is2 ? document.createElementNS(namespace, tag2, { is: is2 }) : document.createElementNS(namespace, tag2)
  );
}
function merge_text_nodes(text2) {
  if (
    /** @type {string} */
    text2.nodeValue.length < 65536
  ) {
    return;
  }
  let next2 = text2.nextSibling;
  while (next2 !== null && next2.nodeType === TEXT_NODE) {
    next2.remove();
    text2.nodeValue += /** @type {string} */
    next2.nodeValue;
    next2 = text2.nextSibling;
  }
}

// content/plugins/node_modules/svelte/src/internal/client/error-handling.js
var adjustments = /* @__PURE__ */ new WeakMap();
function handle_error(error) {
  var effect2 = active_effect;
  if (effect2 === null) {
    active_reaction.f |= ERROR_VALUE;
    return error;
  }
  if (dev_fallback_default && error instanceof Error && !adjustments.has(error)) {
    adjustments.set(error, get_adjustments(error, effect2));
  }
  if ((effect2.f & REACTION_RAN) === 0 && (effect2.f & EFFECT) === 0) {
    if (dev_fallback_default && !effect2.parent && error instanceof Error) {
      apply_adjustments(error);
    }
    throw error;
  }
  invoke_error_boundary(error, effect2);
}
function invoke_error_boundary(error, effect2) {
  if (effect2 !== null && (effect2.f & DESTROYED) !== 0) {
    return;
  }
  while (effect2 !== null) {
    if ((effect2.f & BOUNDARY_EFFECT) !== 0) {
      if ((effect2.f & REACTION_RAN) === 0) {
        throw error;
      }
      try {
        effect2.b.error(error);
        return;
      } catch (e) {
        error = e;
      }
    }
    effect2 = effect2.parent;
  }
  if (dev_fallback_default && error instanceof Error) {
    apply_adjustments(error);
  }
  throw error;
}
function get_adjustments(error, effect2) {
  const message_descriptor = get_descriptor(error, "message");
  if (message_descriptor && !message_descriptor.configurable) return;
  var indent = is_firefox ? "  " : "	";
  var component_stack = `
${indent}in ${effect2.fn?.name || "<unknown>"}`;
  var context = effect2.ctx;
  while (context !== null) {
    component_stack += `
${indent}in ${context.function?.[FILENAME].split("/").pop()}`;
    context = context.p;
  }
  return {
    message: error.message + `
${component_stack}
`,
    stack: error.stack?.split("\n").filter((line) => !line.includes("svelte/src/internal")).join("\n")
  };
}
function apply_adjustments(error) {
  const adjusted = adjustments.get(error);
  if (adjusted) {
    define_property(error, "message", {
      value: adjusted.message
    });
    define_property(error, "stack", {
      value: adjusted.stack
    });
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/status.js
var STATUS_MASK = ~(DIRTY | MAYBE_DIRTY | CLEAN);
function set_signal_status(signal, status) {
  signal.f = signal.f & STATUS_MASK | status;
}
function update_derived_status(derived2) {
  if ((derived2.f & CONNECTED) !== 0 || derived2.deps === null) {
    set_signal_status(derived2, CLEAN);
  } else {
    set_signal_status(derived2, MAYBE_DIRTY);
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/utils.js
function clear_marked(deps) {
  if (deps === null) return;
  for (const dep of deps) {
    if ((dep.f & DERIVED) === 0 || (dep.f & WAS_MARKED) === 0) {
      continue;
    }
    dep.f ^= WAS_MARKED;
    clear_marked(
      /** @type {Derived} */
      dep.deps
    );
  }
}
function defer_effect(effect2, dirty_effects, maybe_dirty_effects) {
  if ((effect2.f & DIRTY) !== 0) {
    dirty_effects.add(effect2);
  } else if ((effect2.f & MAYBE_DIRTY) !== 0) {
    maybe_dirty_effects.add(effect2);
  }
  clear_marked(effect2.deps);
  set_signal_status(effect2, CLEAN);
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/store.js
var legacy_is_updating_store = false;
var is_store_binding = false;
function capture_store_binding(fn) {
  var previous_is_store_binding = is_store_binding;
  try {
    is_store_binding = false;
    return [fn(), is_store_binding];
  } finally {
    is_store_binding = previous_is_store_binding;
  }
}

// content/plugins/node_modules/svelte/src/reactivity/create-subscriber.js
function createSubscriber(start) {
  let subscribers = 0;
  let version = source(0);
  let stop;
  if (dev_fallback_default) {
    tag(version, "createSubscriber version");
  }
  return () => {
    if (effect_tracking()) {
      get(version);
      render_effect(() => {
        if (subscribers === 0) {
          stop = untrack(() => start(() => increment(version)));
        }
        subscribers += 1;
        return () => {
          queue_micro_task(() => {
            subscribers -= 1;
            if (subscribers === 0) {
              stop?.();
              stop = void 0;
              increment(version);
            }
          });
        };
      });
    }
  };
}

// content/plugins/node_modules/svelte/src/internal/client/dom/blocks/boundary.js
var flags = EFFECT_TRANSPARENT | EFFECT_PRESERVED;
function boundary(node, props, children, transform_error) {
  new Boundary(node, props, children, transform_error);
}
var _anchor, _hydrate_open, _props, _children, _effect, _main_effect, _pending_effect, _failed_effect, _offscreen_fragment, _local_pending_count, _pending_count, _pending_count_update_queued, _dirty_effects, _maybe_dirty_effects, _effect_pending, _effect_pending_subscriber, _Boundary_instances, hydrate_resolved_content_fn, hydrate_failed_content_fn, hydrate_pending_content_fn, render_fn, resolve_fn, run_fn, update_pending_count_fn, handle_error_fn;
var Boundary = class {
  /**
   * @param {TemplateNode} node
   * @param {BoundaryProps} props
   * @param {((anchor: Node) => void)} children
   * @param {((error: unknown) => unknown) | undefined} [transform_error]
   */
  constructor(node, props, children, transform_error) {
    __privateAdd(this, _Boundary_instances);
    /** @type {Boundary | null} */
    __publicField(this, "parent");
    __publicField(this, "is_pending", false);
    /**
     * API-level transformError transform function. Transforms errors before they reach the `failed` snippet.
     * Inherited from parent boundary, or defaults to identity.
     * @type {(error: unknown) => unknown}
     */
    __publicField(this, "transform_error");
    /** @type {TemplateNode} */
    __privateAdd(this, _anchor);
    /** @type {TemplateNode | null} */
    __privateAdd(this, _hydrate_open, hydrating ? hydrate_node : null);
    /** @type {BoundaryProps} */
    __privateAdd(this, _props);
    /** @type {((anchor: Node) => void)} */
    __privateAdd(this, _children);
    /** @type {Effect} */
    __privateAdd(this, _effect);
    /** @type {Effect | null} */
    __privateAdd(this, _main_effect, null);
    /** @type {Effect | null} */
    __privateAdd(this, _pending_effect, null);
    /** @type {Effect | null} */
    __privateAdd(this, _failed_effect, null);
    /** @type {DocumentFragment | null} */
    __privateAdd(this, _offscreen_fragment, null);
    __privateAdd(this, _local_pending_count, 0);
    __privateAdd(this, _pending_count, 0);
    __privateAdd(this, _pending_count_update_queued, false);
    /** @type {Set<Effect>} */
    __privateAdd(this, _dirty_effects, /* @__PURE__ */ new Set());
    /** @type {Set<Effect>} */
    __privateAdd(this, _maybe_dirty_effects, /* @__PURE__ */ new Set());
    /**
     * A source containing the number of pending async deriveds/expressions.
     * Only created if `$effect.pending()` is used inside the boundary,
     * otherwise updating the source results in needless `Batch.ensure()`
     * calls followed by no-op flushes
     * @type {Source<number> | null}
     */
    __privateAdd(this, _effect_pending, null);
    __privateAdd(this, _effect_pending_subscriber, createSubscriber(() => {
      __privateSet(this, _effect_pending, source(__privateGet(this, _local_pending_count)));
      if (dev_fallback_default) {
        tag(__privateGet(this, _effect_pending), "$effect.pending()");
      }
      return () => {
        __privateSet(this, _effect_pending, null);
      };
    }));
    __privateSet(this, _anchor, node);
    __privateSet(this, _props, props);
    __privateSet(this, _children, (anchor) => {
      var effect2 = (
        /** @type {Effect} */
        active_effect
      );
      effect2.b = this;
      effect2.f |= BOUNDARY_EFFECT;
      children(anchor);
    });
    this.parent = /** @type {Effect} */
    active_effect.b;
    this.transform_error = transform_error ?? this.parent?.transform_error ?? ((e) => e);
    __privateSet(this, _effect, block(() => {
      if (hydrating) {
        const comment2 = (
          /** @type {Comment} */
          __privateGet(this, _hydrate_open)
        );
        hydrate_next();
        const server_rendered_pending = comment2.data === HYDRATION_START_ELSE;
        const server_rendered_failed = comment2.data.startsWith(HYDRATION_START_FAILED);
        if (server_rendered_failed) {
          const serialized_error = JSON.parse(comment2.data.slice(HYDRATION_START_FAILED.length));
          __privateMethod(this, _Boundary_instances, hydrate_failed_content_fn).call(this, serialized_error);
        } else if (server_rendered_pending) {
          __privateMethod(this, _Boundary_instances, hydrate_pending_content_fn).call(this);
        } else {
          __privateMethod(this, _Boundary_instances, hydrate_resolved_content_fn).call(this);
        }
      } else {
        __privateMethod(this, _Boundary_instances, render_fn).call(this);
      }
    }, flags));
    if (hydrating) {
      __privateSet(this, _anchor, hydrate_node);
    }
  }
  /**
   * Defer an effect inside a pending boundary until the boundary resolves
   * @param {Effect} effect
   */
  defer_effect(effect2) {
    defer_effect(effect2, __privateGet(this, _dirty_effects), __privateGet(this, _maybe_dirty_effects));
  }
  /**
   * Returns `false` if the effect exists inside a boundary whose pending snippet is shown
   * @returns {boolean}
   */
  is_rendered() {
    return !this.is_pending && (!this.parent || this.parent.is_rendered());
  }
  has_pending_snippet() {
    return !!__privateGet(this, _props).pending;
  }
  /**
   * Update the source that powers `$effect.pending()` inside this boundary,
   * and controls when the current `pending` snippet (if any) is removed.
   * Do not call from inside the class
   * @param {1 | -1} d
   * @param {Batch} batch
   */
  update_pending_count(d, batch) {
    __privateMethod(this, _Boundary_instances, update_pending_count_fn).call(this, d, batch);
    __privateSet(this, _local_pending_count, __privateGet(this, _local_pending_count) + d);
    if (!__privateGet(this, _effect_pending) || __privateGet(this, _pending_count_update_queued)) return;
    __privateSet(this, _pending_count_update_queued, true);
    queue_micro_task(() => {
      __privateSet(this, _pending_count_update_queued, false);
      if (__privateGet(this, _effect_pending)) {
        internal_set(__privateGet(this, _effect_pending), __privateGet(this, _local_pending_count));
      }
    });
  }
  get_effect_pending() {
    __privateGet(this, _effect_pending_subscriber).call(this);
    return get(
      /** @type {Source<number>} */
      __privateGet(this, _effect_pending)
    );
  }
  /** @param {unknown} error */
  error(error) {
    if (!__privateGet(this, _props).onerror && !__privateGet(this, _props).failed) {
      throw error;
    }
    if (current_batch?.is_fork) {
      if (__privateGet(this, _main_effect)) current_batch.skip_effect(__privateGet(this, _main_effect));
      if (__privateGet(this, _pending_effect)) current_batch.skip_effect(__privateGet(this, _pending_effect));
      if (__privateGet(this, _failed_effect)) current_batch.skip_effect(__privateGet(this, _failed_effect));
      current_batch.oncommit(() => {
        __privateMethod(this, _Boundary_instances, handle_error_fn).call(this, error);
      });
    } else {
      __privateMethod(this, _Boundary_instances, handle_error_fn).call(this, error);
    }
  }
};
_anchor = new WeakMap();
_hydrate_open = new WeakMap();
_props = new WeakMap();
_children = new WeakMap();
_effect = new WeakMap();
_main_effect = new WeakMap();
_pending_effect = new WeakMap();
_failed_effect = new WeakMap();
_offscreen_fragment = new WeakMap();
_local_pending_count = new WeakMap();
_pending_count = new WeakMap();
_pending_count_update_queued = new WeakMap();
_dirty_effects = new WeakMap();
_maybe_dirty_effects = new WeakMap();
_effect_pending = new WeakMap();
_effect_pending_subscriber = new WeakMap();
_Boundary_instances = new WeakSet();
hydrate_resolved_content_fn = function() {
  try {
    __privateSet(this, _main_effect, branch(() => __privateGet(this, _children).call(this, __privateGet(this, _anchor))));
  } catch (error) {
    this.error(error);
  }
};
/**
 * @param {unknown} error The deserialized error from the server's hydration comment
 */
hydrate_failed_content_fn = function(error) {
  const failed = __privateGet(this, _props).failed;
  if (!failed) return;
  __privateSet(this, _failed_effect, branch(() => {
    failed(
      __privateGet(this, _anchor),
      () => error,
      () => () => {
      }
    );
  }));
};
hydrate_pending_content_fn = function() {
  const pending2 = __privateGet(this, _props).pending;
  if (!pending2) return;
  this.is_pending = true;
  __privateSet(this, _pending_effect, branch(() => pending2(__privateGet(this, _anchor))));
  queue_micro_task(() => {
    var fragment = __privateSet(this, _offscreen_fragment, document.createDocumentFragment());
    var anchor = create_text();
    fragment.append(anchor);
    __privateSet(this, _main_effect, __privateMethod(this, _Boundary_instances, run_fn).call(this, () => {
      return branch(() => __privateGet(this, _children).call(this, anchor));
    }));
    if (__privateGet(this, _pending_count) === 0) {
      __privateGet(this, _anchor).before(fragment);
      __privateSet(this, _offscreen_fragment, null);
      pause_effect(
        /** @type {Effect} */
        __privateGet(this, _pending_effect),
        () => {
          __privateSet(this, _pending_effect, null);
        }
      );
      __privateMethod(this, _Boundary_instances, resolve_fn).call(
        this,
        /** @type {Batch} */
        current_batch
      );
    }
  });
};
render_fn = function() {
  try {
    this.is_pending = this.has_pending_snippet();
    __privateSet(this, _pending_count, 0);
    __privateSet(this, _local_pending_count, 0);
    __privateSet(this, _main_effect, branch(() => {
      __privateGet(this, _children).call(this, __privateGet(this, _anchor));
    }));
    if (__privateGet(this, _pending_count) > 0) {
      var fragment = __privateSet(this, _offscreen_fragment, document.createDocumentFragment());
      move_effect(__privateGet(this, _main_effect), fragment);
      const pending2 = (
        /** @type {(anchor: Node) => void} */
        __privateGet(this, _props).pending
      );
      __privateSet(this, _pending_effect, branch(() => pending2(__privateGet(this, _anchor))));
    } else {
      __privateMethod(this, _Boundary_instances, resolve_fn).call(
        this,
        /** @type {Batch} */
        current_batch
      );
    }
  } catch (error) {
    this.error(error);
  }
};
/**
 * @param {Batch} batch
 */
resolve_fn = function(batch) {
  this.is_pending = false;
  batch.transfer_effects(__privateGet(this, _dirty_effects), __privateGet(this, _maybe_dirty_effects));
};
/**
 * @template T
 * @param {() => T} fn
 */
run_fn = function(fn) {
  var previous_effect = active_effect;
  var previous_reaction = active_reaction;
  var previous_ctx = component_context;
  set_active_effect(__privateGet(this, _effect));
  set_active_reaction(__privateGet(this, _effect));
  set_component_context(__privateGet(this, _effect).ctx);
  try {
    Batch.ensure();
    return fn();
  } catch (e) {
    handle_error(e);
    return null;
  } finally {
    set_active_effect(previous_effect);
    set_active_reaction(previous_reaction);
    set_component_context(previous_ctx);
  }
};
/**
 * Updates the pending count associated with the currently visible pending snippet,
 * if any, such that we can replace the snippet with content once work is done
 * @param {1 | -1} d
 * @param {Batch} batch
 */
update_pending_count_fn = function(d, batch) {
  var _a2;
  if (!this.has_pending_snippet()) {
    if (this.parent) {
      __privateMethod(_a2 = this.parent, _Boundary_instances, update_pending_count_fn).call(_a2, d, batch);
    }
    return;
  }
  __privateSet(this, _pending_count, __privateGet(this, _pending_count) + d);
  if (__privateGet(this, _pending_count) === 0) {
    __privateMethod(this, _Boundary_instances, resolve_fn).call(this, batch);
    if (__privateGet(this, _pending_effect)) {
      pause_effect(__privateGet(this, _pending_effect), () => {
        __privateSet(this, _pending_effect, null);
      });
    }
    if (__privateGet(this, _offscreen_fragment)) {
      __privateGet(this, _anchor).before(__privateGet(this, _offscreen_fragment));
      __privateSet(this, _offscreen_fragment, null);
    }
  }
};
/**
 * @param {unknown} error
 */
handle_error_fn = function(error) {
  if (__privateGet(this, _main_effect)) {
    destroy_effect(__privateGet(this, _main_effect));
    __privateSet(this, _main_effect, null);
  }
  if (__privateGet(this, _pending_effect)) {
    destroy_effect(__privateGet(this, _pending_effect));
    __privateSet(this, _pending_effect, null);
  }
  if (__privateGet(this, _failed_effect)) {
    destroy_effect(__privateGet(this, _failed_effect));
    __privateSet(this, _failed_effect, null);
  }
  if (hydrating) {
    set_hydrate_node(
      /** @type {TemplateNode} */
      __privateGet(this, _hydrate_open)
    );
    next();
    set_hydrate_node(skip_nodes());
  }
  var onerror = __privateGet(this, _props).onerror;
  let failed = __privateGet(this, _props).failed;
  var did_reset = false;
  var calling_on_error = false;
  const reset2 = () => {
    if (did_reset) {
      svelte_boundary_reset_noop();
      return;
    }
    did_reset = true;
    if (calling_on_error) {
      svelte_boundary_reset_onerror();
    }
    if (__privateGet(this, _failed_effect) !== null) {
      pause_effect(__privateGet(this, _failed_effect), () => {
        __privateSet(this, _failed_effect, null);
      });
    }
    __privateMethod(this, _Boundary_instances, run_fn).call(this, () => {
      __privateMethod(this, _Boundary_instances, render_fn).call(this);
    });
  };
  const handle_error_result = (transformed_error) => {
    try {
      calling_on_error = true;
      onerror?.(transformed_error, reset2);
      calling_on_error = false;
    } catch (error2) {
      invoke_error_boundary(error2, __privateGet(this, _effect) && __privateGet(this, _effect).parent);
    }
    if (failed) {
      __privateSet(this, _failed_effect, __privateMethod(this, _Boundary_instances, run_fn).call(this, () => {
        try {
          return branch(() => {
            var effect2 = (
              /** @type {Effect} */
              active_effect
            );
            effect2.b = this;
            effect2.f |= BOUNDARY_EFFECT;
            failed(
              __privateGet(this, _anchor),
              () => transformed_error,
              () => reset2
            );
          });
        } catch (error2) {
          invoke_error_boundary(
            error2,
            /** @type {Effect} */
            __privateGet(this, _effect).parent
          );
          return null;
        }
      }));
    }
  };
  queue_micro_task(() => {
    var result;
    try {
      result = this.transform_error(error);
    } catch (e) {
      invoke_error_boundary(e, __privateGet(this, _effect) && __privateGet(this, _effect).parent);
      return;
    }
    if (result !== null && typeof result === "object" && typeof /** @type {any} */
    result.then === "function") {
      result.then(
        handle_error_result,
        /** @param {unknown} e */
        (e) => invoke_error_boundary(e, __privateGet(this, _effect) && __privateGet(this, _effect).parent)
      );
    } else {
      handle_error_result(result);
    }
  });
};

// content/plugins/node_modules/svelte/src/internal/client/reactivity/async.js
function flatten(blockers, sync, async2, fn) {
  const d = is_runes() ? derived : derived_safe_equal;
  var pending2 = blockers.filter((b) => !b.settled);
  var deriveds = sync.map(d);
  if (dev_fallback_default) {
    deriveds.forEach((d2, i) => {
      d2.label = sync[i].toString().replace("() => ", "").replaceAll("$.eager(() => ", "$state.eager(").replace(/\$\.get\((.+?)\)/g, (_, id) => id);
    });
  }
  if (async2.length === 0 && pending2.length === 0) {
    fn(deriveds);
    return;
  }
  var parent = (
    /** @type {Effect} */
    active_effect
  );
  var restore = capture();
  var blocker_promise = pending2.length === 1 ? pending2[0].promise : pending2.length > 1 ? Promise.all(pending2.map((b) => b.promise)) : null;
  function finish(async3) {
    if ((parent.f & DESTROYED) !== 0) {
      return;
    }
    restore();
    try {
      fn([...deriveds, ...async3]);
    } catch (error) {
      invoke_error_boundary(error, parent);
    }
    unset_context();
  }
  var decrement_pending = increment_pending();
  if (async2.length === 0) {
    blocker_promise.then(() => finish([])).finally(decrement_pending);
    return;
  }
  function run3() {
    Promise.all(async2.map((expression) => async_derived(expression))).then(finish).catch((error) => invoke_error_boundary(error, parent)).finally(decrement_pending);
  }
  if (blocker_promise) {
    blocker_promise.then(() => {
      restore();
      run3();
      unset_context();
    });
  } else {
    run3();
  }
}
function capture() {
  var previous_effect = (
    /** @type {Effect} */
    active_effect
  );
  var previous_reaction = active_reaction;
  var previous_component_context = component_context;
  var previous_batch2 = (
    /** @type {Batch} */
    current_batch
  );
  if (dev_fallback_default) {
    var previous_dev_stack = dev_stack;
  }
  return function restore(activate_batch = true) {
    set_active_effect(previous_effect);
    set_active_reaction(previous_reaction);
    set_component_context(previous_component_context);
    if (activate_batch && (previous_effect.f & DESTROYED) === 0) {
      previous_batch2?.activate();
      previous_batch2?.apply();
    }
    if (dev_fallback_default) {
      set_reactivity_loss_tracker(null);
      set_dev_stack(previous_dev_stack);
    }
  };
}
function unset_context(deactivate_batch = true) {
  set_active_effect(null);
  set_active_reaction(null);
  set_component_context(null);
  if (deactivate_batch) current_batch?.deactivate();
  if (dev_fallback_default) {
    set_reactivity_loss_tracker(null);
    set_dev_stack(null);
  }
}
function increment_pending() {
  var effect2 = (
    /** @type {Effect} */
    active_effect
  );
  var boundary2 = effect2.b;
  var batch = (
    /** @type {Batch} */
    current_batch
  );
  var blocking = !!boundary2?.is_rendered();
  boundary2?.update_pending_count(1, batch);
  batch.increment(blocking, effect2);
  return () => {
    boundary2?.update_pending_count(-1, batch);
    batch.decrement(blocking, effect2);
  };
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/deriveds.js
var reactivity_loss_tracker = null;
function set_reactivity_loss_tracker(v) {
  reactivity_loss_tracker = v;
}
var recent_async_deriveds = /* @__PURE__ */ new Set();
// @__NO_SIDE_EFFECTS__
function derived(fn) {
  var flags2 = DERIVED | DIRTY;
  if (active_effect !== null) {
    active_effect.f |= EFFECT_PRESERVED;
  }
  const signal = {
    ctx: component_context,
    deps: null,
    effects: null,
    equals,
    f: flags2,
    fn,
    reactions: null,
    rv: 0,
    v: (
      /** @type {V} */
      UNINITIALIZED
    ),
    wv: 0,
    parent: active_effect,
    ac: null
  };
  if (dev_fallback_default && tracing_mode_flag) {
    signal.created = get_error("created at");
  }
  return signal;
}
var OBSOLETE = /* @__PURE__ */ Symbol("obsolete");
// @__NO_SIDE_EFFECTS__
function async_derived(fn, label, location) {
  let parent = (
    /** @type {Effect | null} */
    active_effect
  );
  if (parent === null) {
    async_derived_orphan();
  }
  var promise = (
    /** @type {Promise<V>} */
    /** @type {unknown} */
    void 0
  );
  var signal = source(
    /** @type {V} */
    UNINITIALIZED
  );
  if (dev_fallback_default) signal.label = label ?? fn.toString();
  var should_suspend = !active_reaction;
  var deferreds = /* @__PURE__ */ new Set();
  async_effect(() => {
    var effect2 = (
      /** @type {Effect} */
      active_effect
    );
    if (dev_fallback_default) {
      reactivity_loss_tracker = { effect: effect2, effect_deps: /* @__PURE__ */ new Set(), warned: false };
    }
    var d = deferred();
    promise = d.promise;
    try {
      Promise.resolve(fn()).then(d.resolve, (e) => {
        if (e !== STALE_REACTION) d.reject(e);
      }).finally(unset_context);
    } catch (error) {
      d.reject(error);
      unset_context();
    }
    if (dev_fallback_default) {
      if (reactivity_loss_tracker) {
        if (effect2.deps !== null) {
          for (let i = 0; i < skipped_deps; i += 1) {
            reactivity_loss_tracker.effect_deps.add(effect2.deps[i]);
          }
        }
        if (new_deps !== null) {
          for (let i = 0; i < new_deps.length; i += 1) {
            reactivity_loss_tracker.effect_deps.add(new_deps[i]);
          }
        }
      }
      reactivity_loss_tracker = null;
    }
    var batch = (
      /** @type {Batch} */
      current_batch
    );
    if (should_suspend) {
      if ((effect2.f & REACTION_RAN) !== 0) {
        var decrement_pending = increment_pending();
      }
      if (
        // boundary can be null if the async derived is inside an $effect.root not connected to the component render tree
        parent.b?.is_rendered()
      ) {
        batch.async_deriveds.get(effect2)?.reject(OBSOLETE);
      } else {
        for (const d2 of deferreds.values()) {
          d2.reject(OBSOLETE);
        }
      }
      deferreds.add(d);
      batch.async_deriveds.set(effect2, d);
    }
    const handler = (value, error = void 0) => {
      if (dev_fallback_default) {
        reactivity_loss_tracker = null;
      }
      decrement_pending?.();
      deferreds.delete(d);
      if (error === OBSOLETE) return;
      batch.activate();
      if (error) {
        signal.f |= ERROR_VALUE;
        internal_set(signal, error);
      } else {
        if ((signal.f & ERROR_VALUE) !== 0) {
          signal.f ^= ERROR_VALUE;
        }
        if (dev_fallback_default && location !== void 0 && !signal.equals(value)) {
          recent_async_deriveds.add(signal);
          setTimeout(() => {
            if (recent_async_deriveds.has(signal) && (effect2.f & DESTROYED) === 0) {
              await_waterfall(
                /** @type {string} */
                signal.label,
                location
              );
              recent_async_deriveds.delete(signal);
            }
          });
        }
        internal_set(signal, value);
      }
      batch.deactivate();
    };
    d.promise.then(handler, (e) => handler(null, e || "unknown"));
  });
  teardown(() => {
    for (const d of deferreds) {
      d.reject(OBSOLETE);
    }
  });
  if (dev_fallback_default) {
    signal.f |= ASYNC;
  }
  return new Promise((fulfil) => {
    function next2(p) {
      function go() {
        if (p === promise) {
          fulfil(signal);
        } else {
          next2(promise);
        }
      }
      p.then(go, go);
    }
    next2(promise);
  });
}
// @__NO_SIDE_EFFECTS__
function user_derived(fn) {
  const d = /* @__PURE__ */ derived(fn);
  if (!async_mode_flag) push_reaction_value(d);
  return d;
}
// @__NO_SIDE_EFFECTS__
function derived_safe_equal(fn) {
  const signal = /* @__PURE__ */ derived(fn);
  signal.equals = safe_equals;
  return signal;
}
function destroy_derived_effects(derived2) {
  var effects = derived2.effects;
  if (effects !== null) {
    derived2.effects = null;
    for (var i = 0; i < effects.length; i += 1) {
      destroy_effect(
        /** @type {Effect} */
        effects[i]
      );
    }
  }
}
var stack = [];
function execute_derived(derived2) {
  var value;
  var prev_active_effect = active_effect;
  var parent = derived2.parent;
  if (!is_destroying_effect && parent !== null && derived2.v !== UNINITIALIZED && // if it was never evaluated before, it's guaranteed to fail downstream, so we try to execute instead
  (parent.f & (DESTROYED | INERT)) !== 0) {
    derived_inert();
    return derived2.v;
  }
  set_active_effect(parent);
  if (dev_fallback_default) {
    let prev_eager_effects = eager_effects;
    set_eager_effects(/* @__PURE__ */ new Set());
    try {
      if (includes.call(stack, derived2)) {
        derived_references_self();
      }
      stack.push(derived2);
      derived2.f &= ~WAS_MARKED;
      destroy_derived_effects(derived2);
      value = update_reaction(derived2);
    } finally {
      set_active_effect(prev_active_effect);
      set_eager_effects(prev_eager_effects);
      stack.pop();
    }
  } else {
    try {
      derived2.f &= ~WAS_MARKED;
      destroy_derived_effects(derived2);
      value = update_reaction(derived2);
    } finally {
      set_active_effect(prev_active_effect);
    }
  }
  return value;
}
function update_derived(derived2) {
  var value = execute_derived(derived2);
  if (!derived2.equals(value)) {
    derived2.wv = increment_write_version();
    if (!current_batch?.is_fork || derived2.deps === null) {
      if (current_batch !== null) {
        current_batch.capture(derived2, value, true);
        previous_batch?.capture(derived2, value, true);
      } else {
        derived2.v = value;
      }
      if (derived2.deps === null) {
        set_signal_status(derived2, CLEAN);
        return;
      }
    }
  }
  if (is_destroying_effect) {
    return;
  }
  if (batch_values !== null) {
    if (effect_tracking() || current_batch?.is_fork) {
      batch_values.set(derived2, value);
    }
  } else {
    update_derived_status(derived2);
  }
}
function freeze_derived_effects(derived2) {
  if (derived2.effects === null) return;
  for (const e of derived2.effects) {
    if (e.teardown || e.ac) {
      e.teardown?.();
      e.ac?.abort(STALE_REACTION);
      if (e.fn !== null) e.teardown = noop;
      e.ac = null;
      remove_reactions(e, 0);
      destroy_effect_children(e);
    }
  }
}
function unfreeze_derived_effects(derived2) {
  if (derived2.effects === null) return;
  for (const e of derived2.effects) {
    if (e.teardown && e.fn !== null) {
      update_effect(e);
    }
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/batch.js
var first_batch = null;
var last_batch = null;
var current_batch = null;
var previous_batch = null;
var batch_values = null;
var last_scheduled_effect = null;
var is_flushing_sync = false;
var is_processing = false;
var collected_effects = null;
var legacy_updates = null;
var flush_count = 0;
var source_stacks = /* @__PURE__ */ new Set();
var uid = 1;
var _started, _prev, _next, _commit_callbacks, _discard_callbacks, _pending, _blocking_pending, _deferred, _roots, _new_effects, _dirty_effects2, _maybe_dirty_effects2, _skipped_branches, _unskipped_branches, _decrement_queued, _Batch_instances, is_deferred_fn, process_fn, traverse_fn, find_earlier_batch_fn, merge_fn, defer_effects_fn, commit_fn, unlink_fn;
var _Batch = class _Batch {
  constructor() {
    __privateAdd(this, _Batch_instances);
    __publicField(this, "id", uid++);
    /** True as soon as `#process` was called */
    __privateAdd(this, _started, false);
    __publicField(this, "linked", true);
    /** @type {Batch | null} */
    __privateAdd(this, _prev, null);
    /** @type {Batch | null} */
    __privateAdd(this, _next, null);
    /** @type {Map<Effect, ReturnType<typeof deferred<any>>>} */
    __publicField(this, "async_deriveds", /* @__PURE__ */ new Map());
    /**
     * The current values of any signals that are updated in this batch.
     * Tuple format: [value, is_derived] (note: is_derived is false for deriveds, too, if they were overridden via assignment)
     * They keys of this map are identical to `this.#previous`
     * @type {Map<Value, [any, boolean]>}
     */
    __publicField(this, "current", /* @__PURE__ */ new Map());
    /**
     * The values of any signals (sources and deriveds) that are updated in this batch _before_ those updates took place.
     * They keys of this map are identical to `this.#current`
     * @type {Map<Value, any>}
     */
    __publicField(this, "previous", /* @__PURE__ */ new Map());
    /**
     * When the batch is committed (and the DOM is updated), we need to remove old branches
     * and append new ones by calling the functions added inside (if/each/key/etc) blocks
     * @type {Set<(batch: Batch) => void>}
     */
    __privateAdd(this, _commit_callbacks, /* @__PURE__ */ new Set());
    /**
     * If a fork is discarded, we need to destroy any effects that are no longer needed
     * @type {Set<(batch: Batch) => void>}
     */
    __privateAdd(this, _discard_callbacks, /* @__PURE__ */ new Set());
    /**
     * The number of async effects that are currently in flight
     */
    __privateAdd(this, _pending, 0);
    /**
     * Async effects that are currently in flight, _not_ inside a pending boundary
     * @type {Map<Effect, number>}
     */
    __privateAdd(this, _blocking_pending, /* @__PURE__ */ new Map());
    /**
     * A deferred that resolves when the batch is committed, used with `settled()`
     * TODO replace with Promise.withResolvers once supported widely enough
     * @type {{ promise: Promise<void>, resolve: (value?: any) => void, reject: (reason: unknown) => void } | null}
     */
    __privateAdd(this, _deferred, null);
    /**
     * The root effects that need to be flushed
     * @type {Effect[]}
     */
    __privateAdd(this, _roots, []);
    /**
     * Effects created while this batch was active.
     * @type {Effect[]}
     */
    __privateAdd(this, _new_effects, []);
    /**
     * Deferred effects (which run after async work has completed) that are DIRTY
     * @type {Set<Effect>}
     */
    __privateAdd(this, _dirty_effects2, /* @__PURE__ */ new Set());
    /**
     * Deferred effects that are MAYBE_DIRTY
     * @type {Set<Effect>}
     */
    __privateAdd(this, _maybe_dirty_effects2, /* @__PURE__ */ new Set());
    /**
     * A map of branches that still exist, but will be destroyed when this batch
     * is committed — we skip over these during `process`.
     * The value contains child effects that were dirty/maybe_dirty before being reset,
     * so they can be rescheduled if the branch survives.
     * @type {Map<Effect, { d: Effect[], m: Effect[] }>}
     */
    __privateAdd(this, _skipped_branches, /* @__PURE__ */ new Map());
    /**
     * Inverse of #skipped_branches which we need to tell prior batches to unskip them when committing
     * @type {Set<Effect>}
     */
    __privateAdd(this, _unskipped_branches, /* @__PURE__ */ new Set());
    __publicField(this, "is_fork", false);
    __privateAdd(this, _decrement_queued, false);
    if (last_batch === null) {
      first_batch = last_batch = this;
    } else {
      __privateSet(last_batch, _next, this);
      __privateSet(this, _prev, last_batch);
    }
    last_batch = this;
  }
  /**
   * Add an effect to the #skipped_branches map and reset its children
   * @param {Effect} effect
   */
  skip_effect(effect2) {
    if (!__privateGet(this, _skipped_branches).has(effect2)) {
      __privateGet(this, _skipped_branches).set(effect2, { d: [], m: [] });
    }
    __privateGet(this, _unskipped_branches).delete(effect2);
  }
  /**
   * Remove an effect from the #skipped_branches map and reschedule
   * any tracked dirty/maybe_dirty child effects
   * @param {Effect} effect
   * @param {(e: Effect) => void} callback
   */
  unskip_effect(effect2, callback = (e) => this.schedule(e)) {
    var tracked = __privateGet(this, _skipped_branches).get(effect2);
    if (tracked) {
      __privateGet(this, _skipped_branches).delete(effect2);
      for (var e of tracked.d) {
        set_signal_status(e, DIRTY);
        callback(e);
      }
      for (e of tracked.m) {
        set_signal_status(e, MAYBE_DIRTY);
        callback(e);
      }
    }
    __privateGet(this, _unskipped_branches).add(effect2);
  }
  /**
   * Associate a change to a given source with the current
   * batch, noting its previous and current values
   * @param {Value} source
   * @param {any} value
   * @param {boolean} [is_derived]
   */
  capture(source2, value, is_derived = false) {
    if (source2.v !== UNINITIALIZED && !this.previous.has(source2)) {
      this.previous.set(source2, source2.v);
    }
    if ((source2.f & ERROR_VALUE) === 0) {
      this.current.set(source2, [value, is_derived]);
      batch_values?.set(source2, value);
    }
    if (!this.is_fork) {
      source2.v = value;
    }
  }
  activate() {
    current_batch = this;
  }
  deactivate() {
    current_batch = null;
    batch_values = null;
  }
  flush() {
    try {
      if (dev_fallback_default) {
        source_stacks.clear();
      }
      is_processing = true;
      current_batch = this;
      __privateMethod(this, _Batch_instances, process_fn).call(this);
    } finally {
      flush_count = 0;
      last_scheduled_effect = null;
      collected_effects = null;
      legacy_updates = null;
      is_processing = false;
      current_batch = null;
      batch_values = null;
      old_values.clear();
      if (dev_fallback_default) {
        for (const source2 of source_stacks) {
          source2.updated = null;
        }
      }
    }
  }
  discard() {
    for (const fn of __privateGet(this, _discard_callbacks)) fn(this);
    __privateGet(this, _discard_callbacks).clear();
    for (const deferred2 of this.async_deriveds.values()) {
      deferred2.reject(OBSOLETE);
    }
    __privateMethod(this, _Batch_instances, unlink_fn).call(this);
    __privateGet(this, _deferred)?.resolve();
  }
  /**
   * @param {Effect} effect
   */
  register_created_effect(effect2) {
    __privateGet(this, _new_effects).push(effect2);
  }
  /**
   * @param {boolean} blocking
   * @param {Effect} effect
   */
  increment(blocking, effect2) {
    __privateSet(this, _pending, __privateGet(this, _pending) + 1);
    if (blocking) {
      let blocking_pending_count = __privateGet(this, _blocking_pending).get(effect2) ?? 0;
      __privateGet(this, _blocking_pending).set(effect2, blocking_pending_count + 1);
    }
  }
  /**
   * @param {boolean} blocking
   * @param {Effect} effect
   */
  decrement(blocking, effect2) {
    __privateSet(this, _pending, __privateGet(this, _pending) - 1);
    if (blocking) {
      let blocking_pending_count = __privateGet(this, _blocking_pending).get(effect2) ?? 0;
      if (blocking_pending_count === 1) {
        __privateGet(this, _blocking_pending).delete(effect2);
      } else {
        __privateGet(this, _blocking_pending).set(effect2, blocking_pending_count - 1);
      }
    }
    if (__privateGet(this, _decrement_queued)) return;
    __privateSet(this, _decrement_queued, true);
    queue_micro_task(() => {
      __privateSet(this, _decrement_queued, false);
      if (this.linked) {
        this.flush();
      }
    });
  }
  /**
   * @param {Set<Effect>} dirty_effects
   * @param {Set<Effect>} maybe_dirty_effects
   */
  transfer_effects(dirty_effects, maybe_dirty_effects) {
    for (const e of dirty_effects) {
      __privateGet(this, _dirty_effects2).add(e);
    }
    for (const e of maybe_dirty_effects) {
      __privateGet(this, _maybe_dirty_effects2).add(e);
    }
    dirty_effects.clear();
    maybe_dirty_effects.clear();
  }
  /** @param {(batch: Batch) => void} fn */
  oncommit(fn) {
    __privateGet(this, _commit_callbacks).add(fn);
  }
  /** @param {(batch: Batch) => void} fn */
  ondiscard(fn) {
    __privateGet(this, _discard_callbacks).add(fn);
  }
  settled() {
    return (__privateGet(this, _deferred) ?? __privateSet(this, _deferred, deferred())).promise;
  }
  static ensure() {
    if (current_batch === null) {
      const batch = current_batch = new _Batch();
      if (!is_processing && !is_flushing_sync) {
        queue_micro_task(() => {
          if (!__privateGet(batch, _started)) {
            batch.flush();
          }
        });
      }
    }
    return current_batch;
  }
  apply() {
    if (!async_mode_flag || !this.is_fork && __privateGet(this, _prev) === null && __privateGet(this, _next) === null) {
      batch_values = null;
      return;
    }
    batch_values = /* @__PURE__ */ new Map();
    for (const [source2, [value]] of this.current) {
      batch_values.set(source2, value);
    }
    for (let batch = first_batch; batch !== null; batch = __privateGet(batch, _next)) {
      if (batch === this || batch.is_fork) continue;
      var intersects = false;
      if (batch.id < this.id) {
        for (const [source2, [, is_derived]] of batch.current) {
          if (is_derived) continue;
          if (this.current.has(source2)) {
            intersects = true;
            break;
          }
        }
      }
      if (!intersects) {
        for (const [source2, previous] of batch.previous) {
          if (!batch_values.has(source2)) {
            batch_values.set(source2, previous);
          }
        }
      }
    }
  }
  /**
   *
   * @param {Effect} effect
   */
  schedule(effect2) {
    last_scheduled_effect = effect2;
    if (effect2.b?.is_pending && (effect2.f & (EFFECT | RENDER_EFFECT | MANAGED_EFFECT)) !== 0 && (effect2.f & REACTION_RAN) === 0) {
      effect2.b.defer_effect(effect2);
      return;
    }
    var e = effect2;
    while (e.parent !== null) {
      e = e.parent;
      var flags2 = e.f;
      if (collected_effects !== null && e === active_effect) {
        if (async_mode_flag) return;
        if ((active_reaction === null || (active_reaction.f & DERIVED) === 0) && !legacy_is_updating_store) {
          return;
        }
      }
      if ((flags2 & (ROOT_EFFECT | BRANCH_EFFECT)) !== 0) {
        if ((flags2 & CLEAN) === 0) {
          return;
        }
        e.f ^= CLEAN;
      }
    }
    __privateGet(this, _roots).push(e);
  }
};
_started = new WeakMap();
_prev = new WeakMap();
_next = new WeakMap();
_commit_callbacks = new WeakMap();
_discard_callbacks = new WeakMap();
_pending = new WeakMap();
_blocking_pending = new WeakMap();
_deferred = new WeakMap();
_roots = new WeakMap();
_new_effects = new WeakMap();
_dirty_effects2 = new WeakMap();
_maybe_dirty_effects2 = new WeakMap();
_skipped_branches = new WeakMap();
_unskipped_branches = new WeakMap();
_decrement_queued = new WeakMap();
_Batch_instances = new WeakSet();
is_deferred_fn = function() {
  if (this.is_fork) return true;
  for (const effect2 of __privateGet(this, _blocking_pending).keys()) {
    var e = effect2;
    var skipped = false;
    while (e.parent !== null) {
      if (__privateGet(this, _skipped_branches).has(e)) {
        skipped = true;
        break;
      }
      e = e.parent;
    }
    if (!skipped) {
      return true;
    }
  }
  return false;
};
process_fn = function() {
  var _a2, _b, _c;
  __privateSet(this, _started, true);
  if (flush_count++ > 1e3) {
    __privateMethod(this, _Batch_instances, unlink_fn).call(this);
    infinite_loop_guard();
  }
  if (dev_fallback_default) {
    for (const value of this.current.keys()) {
      source_stacks.add(value);
    }
  }
  for (const e of __privateGet(this, _dirty_effects2)) {
    __privateGet(this, _maybe_dirty_effects2).delete(e);
    set_signal_status(e, DIRTY);
    this.schedule(e);
  }
  for (const e of __privateGet(this, _maybe_dirty_effects2)) {
    set_signal_status(e, MAYBE_DIRTY);
    this.schedule(e);
  }
  const roots = __privateGet(this, _roots);
  __privateSet(this, _roots, []);
  this.apply();
  var effects = collected_effects = [];
  var render_effects = [];
  var updates = legacy_updates = [];
  for (const root2 of roots) {
    try {
      __privateMethod(this, _Batch_instances, traverse_fn).call(this, root2, effects, render_effects);
    } catch (e) {
      reset_all(root2);
      if (!__privateMethod(this, _Batch_instances, is_deferred_fn).call(this)) this.discard();
      throw e;
    }
  }
  current_batch = null;
  if (updates.length > 0) {
    var batch = _Batch.ensure();
    for (const e of updates) {
      batch.schedule(e);
    }
  }
  collected_effects = null;
  legacy_updates = null;
  if (__privateMethod(this, _Batch_instances, is_deferred_fn).call(this)) {
    __privateMethod(this, _Batch_instances, defer_effects_fn).call(this, render_effects);
    __privateMethod(this, _Batch_instances, defer_effects_fn).call(this, effects);
    for (const [e, t] of __privateGet(this, _skipped_branches)) {
      reset_branch(e, t);
    }
    if (updates.length > 0) {
      /** @type {unknown} */
      __privateMethod(_a2 = current_batch, _Batch_instances, process_fn).call(_a2);
    }
    return;
  }
  const earlier_batch = __privateMethod(this, _Batch_instances, find_earlier_batch_fn).call(this);
  if (earlier_batch) {
    __privateMethod(this, _Batch_instances, defer_effects_fn).call(this, render_effects);
    __privateMethod(this, _Batch_instances, defer_effects_fn).call(this, effects);
    __privateMethod(_b = earlier_batch, _Batch_instances, merge_fn).call(_b, this);
    return;
  }
  __privateGet(this, _dirty_effects2).clear();
  __privateGet(this, _maybe_dirty_effects2).clear();
  for (const fn of __privateGet(this, _commit_callbacks)) fn(this);
  __privateGet(this, _commit_callbacks).clear();
  previous_batch = this;
  flush_queued_effects(render_effects);
  flush_queued_effects(effects);
  previous_batch = null;
  __privateGet(this, _deferred)?.resolve();
  var next_batch = (
    /** @type {Batch | null} */
    /** @type {unknown} */
    current_batch
  );
  if (__privateGet(this, _pending) === 0 && (__privateGet(this, _roots).length === 0 || next_batch !== null)) {
    __privateMethod(this, _Batch_instances, unlink_fn).call(this);
    if (async_mode_flag) {
      __privateMethod(this, _Batch_instances, commit_fn).call(this);
      current_batch = next_batch;
    }
  }
  if (__privateGet(this, _roots).length > 0) {
    if (next_batch !== null) {
      const batch2 = next_batch;
      __privateGet(batch2, _roots).push(...__privateGet(this, _roots).filter((r) => !__privateGet(batch2, _roots).includes(r)));
    } else {
      next_batch = this;
    }
  }
  if (next_batch !== null) {
    __privateMethod(_c = next_batch, _Batch_instances, process_fn).call(_c);
  }
};
/**
 * Traverse the effect tree, executing effects or stashing
 * them for later execution as appropriate
 * @param {Effect} root
 * @param {Effect[]} effects
 * @param {Effect[]} render_effects
 */
traverse_fn = function(root2, effects, render_effects) {
  root2.f ^= CLEAN;
  var effect2 = root2.first;
  while (effect2 !== null) {
    var flags2 = effect2.f;
    var is_branch = (flags2 & (BRANCH_EFFECT | ROOT_EFFECT)) !== 0;
    var is_skippable_branch = is_branch && (flags2 & CLEAN) !== 0;
    var skip = is_skippable_branch || (flags2 & INERT) !== 0 || __privateGet(this, _skipped_branches).has(effect2);
    if (!skip && effect2.fn !== null) {
      if (is_branch) {
        effect2.f ^= CLEAN;
      } else if ((flags2 & EFFECT) !== 0) {
        effects.push(effect2);
      } else if (async_mode_flag && (flags2 & (RENDER_EFFECT | MANAGED_EFFECT)) !== 0) {
        render_effects.push(effect2);
      } else if (is_dirty(effect2)) {
        if ((flags2 & BLOCK_EFFECT) !== 0) __privateGet(this, _maybe_dirty_effects2).add(effect2);
        update_effect(effect2);
      }
      var child2 = effect2.first;
      if (child2 !== null) {
        effect2 = child2;
        continue;
      }
    }
    while (effect2 !== null) {
      var next2 = effect2.next;
      if (next2 !== null) {
        effect2 = next2;
        break;
      }
      effect2 = effect2.parent;
    }
  }
};
find_earlier_batch_fn = function() {
  var batch = __privateGet(this, _prev);
  while (batch !== null) {
    if (!batch.is_fork) {
      for (const [value, [, is_derived]] of this.current) {
        if (batch.current.has(value) && !is_derived) {
          return batch;
        }
      }
    }
    batch = __privateGet(batch, _prev);
  }
  return null;
};
/**
 * @param {Batch} batch
 */
merge_fn = function(batch) {
  var _a2;
  for (const [source2, value] of batch.current) {
    if (!this.previous.has(source2) && batch.previous.has(source2)) {
      this.previous.set(source2, batch.previous.get(source2));
    }
    this.current.set(source2, value);
  }
  for (const [effect2, deferred2] of batch.async_deriveds) {
    const d = this.async_deriveds.get(effect2);
    if (d) deferred2.promise.then(d.resolve).catch(d.reject);
  }
  batch.async_deriveds.clear();
  this.transfer_effects(__privateGet(batch, _dirty_effects2), __privateGet(batch, _maybe_dirty_effects2));
  const mark = (value) => {
    var reactions = value.reactions;
    if (reactions === null) return;
    for (const reaction of reactions) {
      var flags2 = reaction.f;
      if ((flags2 & DERIVED) !== 0) {
        mark(
          /** @type {Derived} */
          reaction
        );
      } else {
        var effect2 = (
          /** @type {Effect} */
          reaction
        );
        if (flags2 & (ASYNC | BLOCK_EFFECT) && !this.async_deriveds.has(effect2)) {
          __privateGet(this, _maybe_dirty_effects2).delete(effect2);
          set_signal_status(effect2, DIRTY);
          this.schedule(effect2);
        }
      }
    }
  };
  for (const source2 of this.current.keys()) {
    mark(source2);
  }
  this.oncommit(() => batch.discard());
  __privateMethod(_a2 = batch, _Batch_instances, unlink_fn).call(_a2);
  current_batch = this;
  __privateMethod(this, _Batch_instances, process_fn).call(this);
};
/**
 * @param {Effect[]} effects
 */
defer_effects_fn = function(effects) {
  for (var i = 0; i < effects.length; i += 1) {
    defer_effect(effects[i], __privateGet(this, _dirty_effects2), __privateGet(this, _maybe_dirty_effects2));
  }
};
commit_fn = function() {
  var _a2;
  for (let batch = first_batch; batch !== null; batch = __privateGet(batch, _next)) {
    var is_earlier = batch.id < this.id;
    var sources = [];
    for (const [source3, [value, is_derived]] of this.current) {
      if (batch.current.has(source3)) {
        var batch_value = (
          /** @type {[any, boolean]} */
          batch.current.get(source3)[0]
        );
        if (is_earlier && value !== batch_value) {
          batch.current.set(source3, [value, is_derived]);
        } else {
          continue;
        }
      }
      sources.push(source3);
    }
    if (is_earlier) {
      for (const [effect2, deferred2] of this.async_deriveds) {
        const d = batch.async_deriveds.get(effect2);
        if (d) deferred2.promise.then(d.resolve).catch(d.reject);
      }
    }
    var current = [...batch.current.keys()].filter(
      (source3) => !/** @type {[any, boolean]} */
      batch.current.get(source3)[1]
    );
    if (!__privateGet(batch, _started) || current.length === 0) continue;
    var others = current.filter((source3) => !this.current.has(source3));
    if (others.length === 0) {
      if (is_earlier) {
        batch.discard();
      }
    } else if (sources.length > 0) {
      if (dev_fallback_default && !__privateGet(batch, _decrement_queued)) {
        invariant(__privateGet(batch, _roots).length === 0, "Batch has scheduled roots");
      }
      if (is_earlier) {
        for (const unskipped of __privateGet(this, _unskipped_branches)) {
          batch.unskip_effect(unskipped, (e) => {
            var _a3;
            if ((e.f & (BLOCK_EFFECT | ASYNC)) !== 0) {
              batch.schedule(e);
            } else {
              __privateMethod(_a3 = batch, _Batch_instances, defer_effects_fn).call(_a3, [e]);
            }
          });
        }
      }
      batch.activate();
      var marked = /* @__PURE__ */ new Set();
      var checked = /* @__PURE__ */ new Map();
      for (var source2 of sources) {
        mark_effects(source2, others, marked, checked);
      }
      checked = /* @__PURE__ */ new Map();
      var current_unequal = [...batch.current].filter(([c, v1]) => {
        const v2 = this.current.get(c);
        if (!v2) return true;
        return v2[0] !== v1[0] || v2[1] !== v1[1];
      }).map(([c]) => c);
      if (current_unequal.length > 0) {
        for (const effect2 of __privateGet(this, _new_effects)) {
          if ((effect2.f & (DESTROYED | INERT | EAGER_EFFECT)) === 0 && depends_on(effect2, current_unequal, checked)) {
            if ((effect2.f & (ASYNC | BLOCK_EFFECT)) !== 0) {
              set_signal_status(effect2, DIRTY);
              batch.schedule(effect2);
            } else {
              __privateGet(batch, _dirty_effects2).add(effect2);
            }
          }
        }
      }
      if (__privateGet(batch, _roots).length > 0 && !__privateGet(batch, _decrement_queued)) {
        batch.apply();
        for (var root2 of __privateGet(batch, _roots)) {
          __privateMethod(_a2 = batch, _Batch_instances, traverse_fn).call(_a2, root2, [], []);
        }
        __privateSet(batch, _roots, []);
      }
      batch.deactivate();
    }
  }
};
unlink_fn = function() {
  if (!this.linked) return;
  var prev = __privateGet(this, _prev);
  var next2 = __privateGet(this, _next);
  if (prev === null) {
    first_batch = next2;
  } else {
    __privateSet(prev, _next, next2);
  }
  if (next2 === null) {
    last_batch = prev;
  } else {
    __privateSet(next2, _prev, prev);
  }
  this.linked = false;
};
var Batch = _Batch;
function flushSync(fn) {
  var was_flushing_sync = is_flushing_sync;
  is_flushing_sync = true;
  try {
    var result;
    if (fn) {
      if (current_batch !== null && !current_batch.is_fork) {
        current_batch.flush();
      }
      result = fn();
    }
    while (true) {
      flush_tasks();
      if (current_batch === null) {
        return (
          /** @type {T} */
          result
        );
      }
      current_batch.flush();
    }
  } finally {
    is_flushing_sync = was_flushing_sync;
  }
}
function infinite_loop_guard() {
  if (dev_fallback_default) {
    var updates = /* @__PURE__ */ new Map();
    for (
      const source2 of
      /** @type {Batch} */
      current_batch.current.keys()
    ) {
      for (const [stack2, update2] of source2.updated ?? []) {
        var entry = updates.get(stack2);
        if (!entry) {
          entry = { error: update2.error, count: 0 };
          updates.set(stack2, entry);
        }
        entry.count += update2.count;
      }
    }
    for (const update2 of updates.values()) {
      if (update2.error) {
        console.error(update2.error);
      }
    }
  }
  try {
    effect_update_depth_exceeded();
  } catch (error) {
    if (dev_fallback_default) {
      define_property(error, "stack", { value: "" });
    }
    invoke_error_boundary(error, last_scheduled_effect);
  }
}
var eager_block_effects = null;
function flush_queued_effects(effects) {
  var length = effects.length;
  if (length === 0) return;
  var i = 0;
  while (i < length) {
    var effect2 = effects[i++];
    if ((effect2.f & (DESTROYED | INERT)) === 0 && is_dirty(effect2)) {
      eager_block_effects = /* @__PURE__ */ new Set();
      update_effect(effect2);
      if (effect2.deps === null && effect2.first === null && effect2.nodes === null && effect2.teardown === null && effect2.ac === null) {
        unlink_effect(effect2);
      }
      if (eager_block_effects?.size > 0) {
        old_values.clear();
        for (const e of eager_block_effects) {
          if ((e.f & (DESTROYED | INERT)) !== 0) continue;
          const ordered_effects = [e];
          let ancestor = e.parent;
          while (ancestor !== null) {
            if (eager_block_effects.has(ancestor)) {
              eager_block_effects.delete(ancestor);
              ordered_effects.push(ancestor);
            }
            ancestor = ancestor.parent;
          }
          for (let j = ordered_effects.length - 1; j >= 0; j--) {
            const e2 = ordered_effects[j];
            if ((e2.f & (DESTROYED | INERT)) !== 0) continue;
            update_effect(e2);
          }
        }
        eager_block_effects.clear();
      }
    }
  }
  eager_block_effects = null;
}
function mark_effects(value, sources, marked, checked) {
  if (marked.has(value)) return;
  marked.add(value);
  if (value.reactions !== null) {
    for (const reaction of value.reactions) {
      const flags2 = reaction.f;
      if ((flags2 & DERIVED) !== 0) {
        mark_effects(
          /** @type {Derived} */
          reaction,
          sources,
          marked,
          checked
        );
      } else if ((flags2 & (ASYNC | BLOCK_EFFECT)) !== 0 && (flags2 & DIRTY) === 0 && depends_on(reaction, sources, checked)) {
        set_signal_status(reaction, DIRTY);
        schedule_effect(
          /** @type {Effect} */
          reaction
        );
      }
    }
  }
}
function depends_on(reaction, sources, checked) {
  const depends = checked.get(reaction);
  if (depends !== void 0) return depends;
  if (reaction.deps !== null) {
    for (const dep of reaction.deps) {
      if (includes.call(sources, dep)) {
        return true;
      }
      if ((dep.f & DERIVED) !== 0 && depends_on(
        /** @type {Derived} */
        dep,
        sources,
        checked
      )) {
        checked.set(
          /** @type {Derived} */
          dep,
          true
        );
        return true;
      }
    }
  }
  checked.set(reaction, false);
  return false;
}
function schedule_effect(effect2) {
  current_batch.schedule(effect2);
}
function reset_branch(effect2, tracked) {
  if ((effect2.f & BRANCH_EFFECT) !== 0 && (effect2.f & CLEAN) !== 0) {
    return;
  }
  if ((effect2.f & DIRTY) !== 0) {
    tracked.d.push(effect2);
  } else if ((effect2.f & MAYBE_DIRTY) !== 0) {
    tracked.m.push(effect2);
  }
  set_signal_status(effect2, CLEAN);
  var e = effect2.first;
  while (e !== null) {
    reset_branch(e, tracked);
    e = e.next;
  }
}
function reset_all(effect2) {
  set_signal_status(effect2, CLEAN);
  var e = effect2.first;
  while (e !== null) {
    reset_all(e);
    e = e.next;
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/sources.js
var eager_effects = /* @__PURE__ */ new Set();
var old_values = /* @__PURE__ */ new Map();
function set_eager_effects(v) {
  eager_effects = v;
}
var eager_effects_deferred = false;
function set_eager_effects_deferred() {
  eager_effects_deferred = true;
}
function source(v, stack2) {
  var signal = {
    f: 0,
    // TODO ideally we could skip this altogether, but it causes type errors
    v,
    reactions: null,
    equals,
    rv: 0,
    wv: 0
  };
  if (dev_fallback_default && tracing_mode_flag) {
    signal.created = stack2 ?? get_error("created at");
    signal.updated = null;
    signal.set_during_effect = false;
    signal.trace = null;
  }
  return signal;
}
// @__NO_SIDE_EFFECTS__
function state(v, stack2) {
  const s = source(v, stack2);
  push_reaction_value(s);
  return s;
}
// @__NO_SIDE_EFFECTS__
function mutable_source(initial_value, immutable = false, trackable = true) {
  var _a2;
  const s = source(initial_value);
  if (!immutable) {
    s.equals = safe_equals;
  }
  if (legacy_mode_flag && trackable && component_context !== null && component_context.l !== null) {
    ((_a2 = component_context.l).s ?? (_a2.s = [])).push(s);
  }
  return s;
}
function set(source2, value, should_proxy = false) {
  if (active_reaction !== null && // since we are untracking the function inside `$inspect.with` we need to add this check
  // to ensure we error if state is set inside an inspect effect
  (!untracking || (active_reaction.f & EAGER_EFFECT) !== 0) && is_runes() && (active_reaction.f & (DERIVED | BLOCK_EFFECT | ASYNC | EAGER_EFFECT)) !== 0 && (current_sources === null || !current_sources.has(source2))) {
    state_unsafe_mutation();
  }
  let new_value = should_proxy ? proxy(value) : value;
  if (dev_fallback_default) {
    tag_proxy(
      new_value,
      /** @type {string} */
      source2.label
    );
  }
  return internal_set(source2, new_value, legacy_updates);
}
function internal_set(source2, value, updated_during_traversal = null) {
  if (!source2.equals(value)) {
    old_values.set(source2, is_destroying_effect ? value : source2.v);
    var batch = Batch.ensure();
    batch.capture(source2, value);
    if (dev_fallback_default) {
      if (tracing_mode_flag || active_effect !== null) {
        source2.updated ?? (source2.updated = /* @__PURE__ */ new Map());
        const count = (source2.updated.get("")?.count ?? 0) + 1;
        source2.updated.set("", { error: (
          /** @type {any} */
          null
        ), count });
        if (tracing_mode_flag || count > 5) {
          const error = get_error("updated at");
          if (error !== null) {
            let entry = source2.updated.get(error.stack);
            if (!entry) {
              entry = { error, count: 0 };
              source2.updated.set(error.stack, entry);
            }
            entry.count++;
          }
        }
      }
      if (active_effect !== null) {
        source2.set_during_effect = true;
      }
    }
    if ((source2.f & DERIVED) !== 0) {
      const derived2 = (
        /** @type {Derived} */
        source2
      );
      if ((source2.f & DIRTY) !== 0) {
        execute_derived(derived2);
      }
      if (batch_values === null) {
        update_derived_status(derived2);
      }
    }
    source2.wv = increment_write_version();
    mark_reactions(source2, DIRTY, updated_during_traversal);
    if (is_runes() && active_effect !== null && (active_effect.f & CLEAN) !== 0 && (active_effect.f & (BRANCH_EFFECT | ROOT_EFFECT)) === 0) {
      if (untracked_writes === null) {
        set_untracked_writes([source2]);
      } else {
        untracked_writes.push(source2);
      }
    }
    if (!batch.is_fork && eager_effects.size > 0 && !eager_effects_deferred) {
      flush_eager_effects();
    }
  }
  return value;
}
function flush_eager_effects() {
  eager_effects_deferred = false;
  for (const effect2 of eager_effects) {
    if ((effect2.f & CLEAN) !== 0) {
      set_signal_status(effect2, MAYBE_DIRTY);
    }
    let dirty;
    try {
      dirty = is_dirty(effect2);
    } catch {
      dirty = true;
    }
    if (dirty) {
      update_effect(effect2);
    }
  }
  eager_effects.clear();
}
function increment(source2) {
  set(source2, source2.v + 1);
}
function mark_reactions(signal, status, updated_during_traversal) {
  var reactions = signal.reactions;
  if (reactions === null) return;
  var runes = is_runes();
  var length = reactions.length;
  for (var i = 0; i < length; i++) {
    var reaction = reactions[i];
    var flags2 = reaction.f;
    if (!runes && reaction === active_effect) continue;
    var not_dirty = (flags2 & DIRTY) === 0;
    if (not_dirty) {
      set_signal_status(reaction, status);
    }
    if ((flags2 & EAGER_EFFECT) !== 0) {
      eager_effects.add(
        /** @type {Effect} */
        reaction
      );
    } else if ((flags2 & DERIVED) !== 0) {
      var derived2 = (
        /** @type {Derived} */
        reaction
      );
      batch_values?.delete(derived2);
      if ((flags2 & WAS_MARKED) === 0) {
        if (flags2 & CONNECTED && (active_effect === null || (active_effect.f & REACTION_IS_UPDATING) === 0)) {
          reaction.f |= WAS_MARKED;
        }
        mark_reactions(derived2, MAYBE_DIRTY, updated_during_traversal);
      }
    } else if (not_dirty) {
      var effect2 = (
        /** @type {Effect} */
        reaction
      );
      if ((flags2 & BLOCK_EFFECT) !== 0 && eager_block_effects !== null) {
        eager_block_effects.add(effect2);
      }
      if (updated_during_traversal !== null) {
        updated_during_traversal.push(effect2);
      } else {
        schedule_effect(effect2);
      }
    }
  }
}

// content/plugins/node_modules/svelte/src/internal/client/legacy.js
var captured_signals = null;

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/misc.js
function remove_textarea_child(dom) {
  if (hydrating && get_first_child(dom) !== null) {
    clear_text_content(dom);
  }
}
var listening_to_form_reset = false;
function add_form_reset_listener() {
  if (!listening_to_form_reset) {
    listening_to_form_reset = true;
    document.addEventListener(
      "reset",
      (evt) => {
        Promise.resolve().then(() => {
          if (!evt.defaultPrevented) {
            for (
              const e of
              /**@type {HTMLFormElement} */
              evt.target.elements
            ) {
              e[FORM_RESET_HANDLER]?.();
            }
          }
        });
      },
      // In the capture phase to guarantee we get noticed of it (no possibility of stopPropagation)
      { capture: true }
    );
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/bindings/shared.js
function without_reactive_context(fn) {
  var previous_reaction = active_reaction;
  var previous_effect = active_effect;
  set_active_reaction(null);
  set_active_effect(null);
  try {
    return fn();
  } finally {
    set_active_reaction(previous_reaction);
    set_active_effect(previous_effect);
  }
}
function listen_to_event_and_reset_event(element2, event2, handler, on_reset = handler) {
  element2.addEventListener(event2, () => without_reactive_context(handler));
  const prev = (
    /** @type {any} */
    element2[FORM_RESET_HANDLER]
  );
  if (prev) {
    element2[FORM_RESET_HANDLER] = () => {
      prev();
      on_reset(true);
    };
  } else {
    element2[FORM_RESET_HANDLER] = () => on_reset(true);
  }
  add_form_reset_listener();
}

// content/plugins/node_modules/svelte/src/internal/client/runtime.js
var is_updating_effect = false;
var is_destroying_effect = false;
function set_is_destroying_effect(value) {
  is_destroying_effect = value;
}
var active_reaction = null;
var untracking = false;
function set_active_reaction(reaction) {
  active_reaction = reaction;
}
var active_effect = null;
function set_active_effect(effect2) {
  active_effect = effect2;
}
var current_sources = null;
function push_reaction_value(value) {
  if (active_reaction !== null && (!async_mode_flag || (active_reaction.f & DERIVED) !== 0)) {
    (current_sources ?? (current_sources = /* @__PURE__ */ new Set())).add(value);
  }
}
var new_deps = null;
var skipped_deps = 0;
var untracked_writes = null;
function set_untracked_writes(value) {
  untracked_writes = value;
}
var write_version = 1;
var read_version = 0;
var update_version = read_version;
function set_update_version(value) {
  update_version = value;
}
function increment_write_version() {
  return ++write_version;
}
function is_dirty(reaction) {
  var flags2 = reaction.f;
  if ((flags2 & DIRTY) !== 0) {
    return true;
  }
  if (flags2 & DERIVED) {
    reaction.f &= ~WAS_MARKED;
  }
  if ((flags2 & MAYBE_DIRTY) !== 0) {
    var dependencies = (
      /** @type {Value[]} */
      reaction.deps
    );
    var length = dependencies.length;
    for (var i = 0; i < length; i++) {
      var dependency = dependencies[i];
      if (is_dirty(
        /** @type {Derived} */
        dependency
      )) {
        update_derived(
          /** @type {Derived} */
          dependency
        );
      }
      if (dependency.wv > reaction.wv) {
        return true;
      }
    }
    if ((flags2 & CONNECTED) !== 0 && // During time traveling we don't want to reset the status so that
    // traversal of the graph in the other batches still happens
    batch_values === null) {
      set_signal_status(reaction, CLEAN);
    }
  }
  return false;
}
function schedule_possible_effect_self_invalidation(signal, effect2, root2 = true) {
  var reactions = signal.reactions;
  if (reactions === null) return;
  if (!async_mode_flag && current_sources !== null && current_sources.has(signal)) {
    return;
  }
  for (var i = 0; i < reactions.length; i++) {
    var reaction = reactions[i];
    if ((reaction.f & DERIVED) !== 0) {
      schedule_possible_effect_self_invalidation(
        /** @type {Derived} */
        reaction,
        effect2,
        false
      );
    } else if (effect2 === reaction) {
      if (root2) {
        set_signal_status(reaction, DIRTY);
      } else if ((reaction.f & CLEAN) !== 0) {
        set_signal_status(reaction, MAYBE_DIRTY);
      }
      schedule_effect(
        /** @type {Effect} */
        reaction
      );
    }
  }
}
function update_reaction(reaction) {
  var _a2;
  var previous_deps = new_deps;
  var previous_skipped_deps = skipped_deps;
  var previous_untracked_writes = untracked_writes;
  var previous_reaction = active_reaction;
  var previous_sources = current_sources;
  var previous_component_context = component_context;
  var previous_untracking = untracking;
  var previous_update_version = update_version;
  var flags2 = reaction.f;
  new_deps = /** @type {null | Value[]} */
  null;
  skipped_deps = 0;
  untracked_writes = null;
  active_reaction = (flags2 & (BRANCH_EFFECT | ROOT_EFFECT)) === 0 ? reaction : null;
  current_sources = null;
  set_component_context(reaction.ctx);
  untracking = false;
  update_version = ++read_version;
  if (reaction.ac !== null) {
    without_reactive_context(() => {
      reaction.ac.abort(STALE_REACTION);
    });
    reaction.ac = null;
  }
  try {
    reaction.f |= REACTION_IS_UPDATING;
    var fn = (
      /** @type {Function} */
      reaction.fn
    );
    var result = fn();
    reaction.f |= REACTION_RAN;
    var deps = reaction.deps;
    var is_fork = current_batch?.is_fork;
    if (new_deps !== null) {
      var i;
      if (!is_fork) {
        remove_reactions(reaction, skipped_deps);
      }
      if (deps !== null && skipped_deps > 0) {
        deps.length = skipped_deps + new_deps.length;
        for (i = 0; i < new_deps.length; i++) {
          deps[skipped_deps + i] = new_deps[i];
        }
      } else {
        reaction.deps = deps = new_deps;
      }
      if (effect_tracking() && (reaction.f & CONNECTED) !== 0) {
        for (i = skipped_deps; i < deps.length; i++) {
          ((_a2 = deps[i]).reactions ?? (_a2.reactions = [])).push(reaction);
        }
      }
    } else if (!is_fork && deps !== null && skipped_deps < deps.length) {
      remove_reactions(reaction, skipped_deps);
      deps.length = skipped_deps;
    }
    if (is_runes() && untracked_writes !== null && !untracking && deps !== null && (reaction.f & (DERIVED | MAYBE_DIRTY | DIRTY)) === 0) {
      for (i = 0; i < /** @type {Source[]} */
      untracked_writes.length; i++) {
        schedule_possible_effect_self_invalidation(
          untracked_writes[i],
          /** @type {Effect} */
          reaction
        );
      }
    }
    if (previous_reaction !== null && previous_reaction !== reaction) {
      read_version++;
      if (previous_reaction.deps !== null) {
        for (let i2 = 0; i2 < previous_skipped_deps; i2 += 1) {
          previous_reaction.deps[i2].rv = read_version;
        }
      }
      if (previous_deps !== null) {
        for (const dep of previous_deps) {
          dep.rv = read_version;
        }
      }
      if (untracked_writes !== null) {
        if (previous_untracked_writes === null) {
          previous_untracked_writes = untracked_writes;
        } else {
          previous_untracked_writes.push(.../** @type {Source[]} */
          untracked_writes);
        }
      }
    }
    if ((reaction.f & ERROR_VALUE) !== 0) {
      reaction.f ^= ERROR_VALUE;
    }
    return result;
  } catch (error) {
    return handle_error(error);
  } finally {
    reaction.f ^= REACTION_IS_UPDATING;
    new_deps = previous_deps;
    skipped_deps = previous_skipped_deps;
    untracked_writes = previous_untracked_writes;
    active_reaction = previous_reaction;
    current_sources = previous_sources;
    set_component_context(previous_component_context);
    untracking = previous_untracking;
    update_version = previous_update_version;
  }
}
function remove_reaction(signal, dependency) {
  let reactions = dependency.reactions;
  if (reactions !== null) {
    var index2 = index_of.call(reactions, signal);
    if (index2 !== -1) {
      var new_length = reactions.length - 1;
      if (new_length === 0) {
        reactions = dependency.reactions = null;
      } else {
        reactions[index2] = reactions[new_length];
        reactions.pop();
      }
    }
  }
  if (reactions === null && (dependency.f & DERIVED) !== 0 && // Destroying a child effect while updating a parent effect can cause a dependency to appear
  // to be unused, when in fact it is used by the currently-updating parent. Checking `new_deps`
  // allows us to skip the expensive work of disconnecting and immediately reconnecting it
  (new_deps === null || !includes.call(new_deps, dependency))) {
    var derived2 = (
      /** @type {Derived} */
      dependency
    );
    if ((derived2.f & CONNECTED) !== 0) {
      derived2.f ^= CONNECTED;
      derived2.f &= ~WAS_MARKED;
    }
    if (derived2.v !== UNINITIALIZED) {
      update_derived_status(derived2);
    }
    freeze_derived_effects(derived2);
    remove_reactions(derived2, 0);
  }
}
function remove_reactions(signal, start_index) {
  var dependencies = signal.deps;
  if (dependencies === null) return;
  for (var i = start_index; i < dependencies.length; i++) {
    remove_reaction(signal, dependencies[i]);
  }
}
function update_effect(effect2) {
  var flags2 = effect2.f;
  if ((flags2 & DESTROYED) !== 0) {
    return;
  }
  set_signal_status(effect2, CLEAN);
  var previous_effect = active_effect;
  var was_updating_effect = is_updating_effect;
  active_effect = effect2;
  is_updating_effect = true;
  if (dev_fallback_default) {
    var previous_component_fn = dev_current_component_function;
    set_dev_current_component_function(effect2.component_function);
    var previous_stack = (
      /** @type {any} */
      dev_stack
    );
    set_dev_stack(effect2.dev_stack ?? dev_stack);
  }
  try {
    if ((flags2 & (BLOCK_EFFECT | MANAGED_EFFECT)) !== 0) {
      destroy_block_effect_children(effect2);
    } else {
      destroy_effect_children(effect2);
    }
    execute_effect_teardown(effect2);
    var teardown2 = update_reaction(effect2);
    effect2.teardown = typeof teardown2 === "function" ? teardown2 : null;
    effect2.wv = write_version;
    if (dev_fallback_default && tracing_mode_flag && (effect2.f & DIRTY) !== 0 && effect2.deps !== null) {
      for (var dep of effect2.deps) {
        if (dep.set_during_effect) {
          dep.wv = increment_write_version();
          dep.set_during_effect = false;
        }
      }
    }
  } finally {
    is_updating_effect = was_updating_effect;
    active_effect = previous_effect;
    if (dev_fallback_default) {
      set_dev_current_component_function(previous_component_fn);
      set_dev_stack(previous_stack);
    }
  }
}
async function tick() {
  if (async_mode_flag) {
    return new Promise((f) => {
      requestAnimationFrame(() => f());
      setTimeout(() => f());
    });
  }
  await Promise.resolve();
  flushSync();
}
function get(signal) {
  var flags2 = signal.f;
  var is_derived = (flags2 & DERIVED) !== 0;
  captured_signals?.add(signal);
  if (active_reaction !== null && !untracking) {
    var destroyed = active_effect !== null && (active_effect.f & DESTROYED) !== 0;
    if (!destroyed && (current_sources === null || !current_sources.has(signal))) {
      var deps = active_reaction.deps;
      if ((active_reaction.f & REACTION_IS_UPDATING) !== 0) {
        if (signal.rv < read_version) {
          signal.rv = read_version;
          if (new_deps === null && deps !== null && deps[skipped_deps] === signal) {
            skipped_deps++;
          } else if (new_deps === null) {
            new_deps = [signal];
          } else {
            new_deps.push(signal);
          }
        }
      } else {
        active_reaction.deps ?? (active_reaction.deps = []);
        if (!includes.call(active_reaction.deps, signal)) {
          active_reaction.deps.push(signal);
        }
        var reactions = signal.reactions;
        if (reactions === null) {
          signal.reactions = [active_reaction];
        } else if (!includes.call(reactions, active_reaction)) {
          reactions.push(active_reaction);
        }
      }
    }
  }
  if (dev_fallback_default) {
    if (!untracking && reactivity_loss_tracker && // By checking that current/previous batch are null we filter out false positives.
    // reactivity_loss_tracker is only reset after a microtask, so if a flush happens
    // before that, we get warnings for things we shouldn't warn on.
    current_batch === null && previous_batch === null && !reactivity_loss_tracker.warned && (reactivity_loss_tracker.effect.f & REACTION_IS_UPDATING) === 0 && !reactivity_loss_tracker.effect_deps.has(signal)) {
      reactivity_loss_tracker.warned = true;
      await_reactivity_loss(
        /** @type {string} */
        signal.label
      );
      var trace2 = get_error("traced at");
      if (trace2) console.warn(trace2);
    }
    recent_async_deriveds.delete(signal);
    if (tracing_mode_flag && !untracking && tracing_expressions !== null && active_reaction !== null && tracing_expressions.reaction === active_reaction) {
      if (signal.trace) {
        signal.trace();
      } else {
        trace2 = get_error("traced at");
        if (trace2) {
          var entry = tracing_expressions.entries.get(signal);
          if (entry === void 0) {
            entry = { traces: [] };
            tracing_expressions.entries.set(signal, entry);
          }
          var last = entry.traces[entry.traces.length - 1];
          if (trace2.stack !== last?.stack) {
            entry.traces.push(trace2);
          }
        }
      }
    }
  }
  if (is_destroying_effect && old_values.has(signal)) {
    return old_values.get(signal);
  }
  if (is_derived) {
    var derived2 = (
      /** @type {Derived} */
      signal
    );
    if (is_destroying_effect) {
      var value = derived2.v;
      if ((derived2.f & CLEAN) === 0 && derived2.reactions !== null || depends_on_old_values(derived2)) {
        value = execute_derived(derived2);
      }
      old_values.set(derived2, value);
      return value;
    }
    var should_connect = (derived2.f & CONNECTED) === 0 && !untracking && active_reaction !== null && (is_updating_effect || (active_reaction.f & CONNECTED) !== 0);
    var is_new = (derived2.f & REACTION_RAN) === 0;
    if (is_dirty(derived2)) {
      if (should_connect) {
        derived2.f |= CONNECTED;
      }
      update_derived(derived2);
    }
    if (should_connect && !is_new) {
      unfreeze_derived_effects(derived2);
      reconnect(derived2);
    }
  }
  if (batch_values?.has(signal)) {
    return batch_values.get(signal);
  }
  if ((signal.f & ERROR_VALUE) !== 0) {
    throw signal.v;
  }
  return signal.v;
}
function reconnect(derived2) {
  derived2.f |= CONNECTED;
  if (derived2.deps === null) return;
  for (const dep of derived2.deps) {
    (dep.reactions ?? (dep.reactions = [])).push(derived2);
    if ((dep.f & DERIVED) !== 0 && (dep.f & CONNECTED) === 0) {
      unfreeze_derived_effects(
        /** @type {Derived} */
        dep
      );
      reconnect(
        /** @type {Derived} */
        dep
      );
    }
  }
}
function depends_on_old_values(derived2) {
  if (derived2.v === UNINITIALIZED) return true;
  if (derived2.deps === null) return false;
  for (const dep of derived2.deps) {
    if (old_values.has(dep)) {
      return true;
    }
    if ((dep.f & DERIVED) !== 0 && depends_on_old_values(
      /** @type {Derived} */
      dep
    )) {
      return true;
    }
  }
  return false;
}
function untrack(fn) {
  var previous_untracking = untracking;
  try {
    untracking = true;
    return fn();
  } finally {
    untracking = previous_untracking;
  }
}
function deep_read_state(value) {
  if (typeof value !== "object" || !value || value instanceof EventTarget) {
    return;
  }
  if (STATE_SYMBOL in value) {
    deep_read(value);
  } else if (!Array.isArray(value)) {
    for (let key2 in value) {
      const prop2 = value[key2];
      if (typeof prop2 === "object" && prop2 && STATE_SYMBOL in prop2) {
        deep_read(prop2);
      }
    }
  }
}
function deep_read(value, visited = /* @__PURE__ */ new Set()) {
  if (typeof value === "object" && value !== null && // We don't want to traverse DOM elements
  !(value instanceof EventTarget) && !visited.has(value)) {
    visited.add(value);
    if (value instanceof Date) {
      value.getTime();
    }
    for (let key2 in value) {
      try {
        deep_read(value[key2], visited);
      } catch (e) {
      }
    }
    const proto = get_prototype_of(value);
    if (proto !== Object.prototype && proto !== Array.prototype && proto !== Map.prototype && proto !== Set.prototype && proto !== Date.prototype) {
      const descriptors = get_descriptors(proto);
      for (let key2 in descriptors) {
        const get3 = descriptors[key2].get;
        if (get3) {
          try {
            get3.call(value);
          } catch (e) {
          }
        }
      }
    }
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/effects.js
function validate_effect(rune) {
  if (active_effect === null) {
    if (active_reaction === null) {
      effect_orphan(rune);
    }
    effect_in_unowned_derived();
  }
  if (is_destroying_effect) {
    effect_in_teardown(rune);
  }
}
function push_effect(effect2, parent_effect) {
  var parent_last = parent_effect.last;
  if (parent_last === null) {
    parent_effect.last = parent_effect.first = effect2;
  } else {
    parent_last.next = effect2;
    effect2.prev = parent_last;
    parent_effect.last = effect2;
  }
}
function create_effect(type, fn) {
  var parent = active_effect;
  if (dev_fallback_default) {
    while (parent !== null && (parent.f & EAGER_EFFECT) !== 0) {
      parent = parent.parent;
    }
  }
  if (parent !== null && (parent.f & INERT) !== 0) {
    type |= INERT;
  }
  var effect2 = {
    ctx: component_context,
    deps: null,
    nodes: null,
    f: type | DIRTY | CONNECTED,
    first: null,
    fn,
    last: null,
    next: null,
    parent,
    b: parent && parent.b,
    prev: null,
    teardown: null,
    wv: 0,
    ac: null
  };
  if (dev_fallback_default) {
    effect2.component_function = dev_current_component_function;
  }
  current_batch?.register_created_effect(effect2);
  var e = effect2;
  if ((type & EFFECT) !== 0) {
    if (collected_effects !== null) {
      collected_effects.push(effect2);
    } else {
      Batch.ensure().schedule(effect2);
    }
  } else if (fn !== null) {
    try {
      update_effect(effect2);
    } catch (e2) {
      destroy_effect(effect2);
      throw e2;
    }
    if (e.deps === null && e.teardown === null && e.nodes === null && e.first === e.last && // either `null`, or a singular child
    (e.f & EFFECT_PRESERVED) === 0) {
      e = e.first;
      if ((type & BLOCK_EFFECT) !== 0 && (type & EFFECT_TRANSPARENT) !== 0 && e !== null) {
        e.f |= EFFECT_TRANSPARENT;
      }
    }
  }
  if (e !== null) {
    e.parent = parent;
    if (parent !== null) {
      push_effect(e, parent);
    }
    if (active_reaction !== null && (active_reaction.f & DERIVED) !== 0 && (type & ROOT_EFFECT) === 0) {
      var derived2 = (
        /** @type {Derived} */
        active_reaction
      );
      (derived2.effects ?? (derived2.effects = [])).push(e);
    }
  }
  return effect2;
}
function effect_tracking() {
  return active_reaction !== null && !untracking;
}
function teardown(fn) {
  const effect2 = create_effect(RENDER_EFFECT, null);
  set_signal_status(effect2, CLEAN);
  effect2.teardown = fn;
  return effect2;
}
function user_effect(fn) {
  validate_effect("$effect");
  if (dev_fallback_default) {
    define_property(fn, "name", {
      value: "$effect"
    });
  }
  var flags2 = (
    /** @type {Effect} */
    active_effect.f
  );
  var defer = !active_reaction && (flags2 & BRANCH_EFFECT) !== 0 && component_context !== null && !component_context.i;
  if (defer) {
    var context = (
      /** @type {ComponentContext} */
      component_context
    );
    (context.e ?? (context.e = [])).push(fn);
  } else {
    return create_user_effect(fn);
  }
}
function create_user_effect(fn) {
  return create_effect(EFFECT | USER_EFFECT, fn);
}
function effect_root(fn) {
  Batch.ensure();
  const effect2 = create_effect(ROOT_EFFECT | EFFECT_PRESERVED, fn);
  return () => {
    destroy_effect(effect2);
  };
}
function component_root(fn) {
  Batch.ensure();
  const effect2 = create_effect(ROOT_EFFECT | EFFECT_PRESERVED, fn);
  return (options = {}) => {
    return new Promise((fulfil) => {
      if (options.outro) {
        pause_effect(effect2, () => {
          destroy_effect(effect2);
          fulfil(void 0);
        });
      } else {
        destroy_effect(effect2);
        fulfil(void 0);
      }
    });
  };
}
function effect(fn) {
  return create_effect(EFFECT, fn);
}
function async_effect(fn) {
  return create_effect(ASYNC | EFFECT_PRESERVED, fn);
}
function render_effect(fn, flags2 = 0) {
  return create_effect(RENDER_EFFECT | flags2, fn);
}
function template_effect(fn, sync = [], async2 = [], blockers = []) {
  flatten(blockers, sync, async2, (values) => {
    create_effect(RENDER_EFFECT, () => {
      fn(...values.map(get));
    });
  });
}
function block(fn, flags2 = 0) {
  var effect2 = create_effect(BLOCK_EFFECT | flags2, fn);
  if (dev_fallback_default) {
    effect2.dev_stack = dev_stack;
  }
  return effect2;
}
function branch(fn) {
  return create_effect(BRANCH_EFFECT | EFFECT_PRESERVED, fn);
}
function execute_effect_teardown(effect2) {
  var teardown2 = effect2.teardown;
  if (teardown2 !== null) {
    const previously_destroying_effect = is_destroying_effect;
    const previous_reaction = active_reaction;
    set_is_destroying_effect(true);
    set_active_reaction(null);
    try {
      teardown2.call(null);
    } finally {
      set_is_destroying_effect(previously_destroying_effect);
      set_active_reaction(previous_reaction);
    }
  }
}
function destroy_effect_children(signal, remove_dom = false) {
  var effect2 = signal.first;
  signal.first = signal.last = null;
  while (effect2 !== null) {
    const controller = effect2.ac;
    if (controller !== null) {
      without_reactive_context(() => {
        controller.abort(STALE_REACTION);
      });
    }
    var next2 = effect2.next;
    if ((effect2.f & ROOT_EFFECT) !== 0) {
      effect2.parent = null;
    } else {
      destroy_effect(effect2, remove_dom);
    }
    effect2 = next2;
  }
}
function destroy_block_effect_children(signal) {
  var effect2 = signal.first;
  while (effect2 !== null) {
    var next2 = effect2.next;
    if ((effect2.f & BRANCH_EFFECT) === 0) {
      destroy_effect(effect2);
    }
    effect2 = next2;
  }
}
function destroy_effect(effect2, remove_dom = true) {
  var removed = false;
  if ((remove_dom || (effect2.f & HEAD_EFFECT) !== 0) && effect2.nodes !== null && effect2.nodes.end !== null) {
    remove_effect_dom(
      effect2.nodes.start,
      /** @type {TemplateNode} */
      effect2.nodes.end
    );
    removed = true;
  }
  effect2.f |= DESTROYING;
  destroy_effect_children(effect2, remove_dom && !removed);
  remove_reactions(effect2, 0);
  var transitions = effect2.nodes && effect2.nodes.t;
  if (transitions !== null) {
    for (const transition2 of transitions) {
      transition2.stop();
    }
  }
  execute_effect_teardown(effect2);
  effect2.f ^= DESTROYING;
  effect2.f |= DESTROYED;
  var parent = effect2.parent;
  if (parent !== null && parent.first !== null) {
    unlink_effect(effect2);
  }
  if (dev_fallback_default) {
    effect2.component_function = null;
  }
  effect2.next = effect2.prev = effect2.teardown = effect2.ctx = effect2.deps = effect2.fn = effect2.nodes = effect2.ac = effect2.b = null;
}
function remove_effect_dom(node, end) {
  while (node !== null) {
    var next2 = node === end ? null : get_next_sibling(node);
    node.remove();
    node = next2;
  }
}
function unlink_effect(effect2) {
  var parent = effect2.parent;
  var prev = effect2.prev;
  var next2 = effect2.next;
  if (prev !== null) prev.next = next2;
  if (next2 !== null) next2.prev = prev;
  if (parent !== null) {
    if (parent.first === effect2) parent.first = next2;
    if (parent.last === effect2) parent.last = prev;
  }
}
function pause_effect(effect2, callback, destroy = true) {
  var transitions = [];
  pause_children(effect2, transitions, true);
  var fn = () => {
    if (destroy) destroy_effect(effect2);
    if (callback) callback();
  };
  var remaining = transitions.length;
  if (remaining > 0) {
    var check = () => --remaining || fn();
    for (var transition2 of transitions) {
      transition2.out(check);
    }
  } else {
    fn();
  }
}
function pause_children(effect2, transitions, local) {
  if ((effect2.f & INERT) !== 0) return;
  effect2.f ^= INERT;
  var t = effect2.nodes && effect2.nodes.t;
  if (t !== null) {
    for (const transition2 of t) {
      if (transition2.is_global || local) {
        transitions.push(transition2);
      }
    }
  }
  var child2 = effect2.first;
  while (child2 !== null) {
    var sibling2 = child2.next;
    if ((child2.f & ROOT_EFFECT) === 0) {
      var transparent = (child2.f & EFFECT_TRANSPARENT) !== 0 || // If this is a branch effect without a block effect parent,
      // it means the parent block effect was pruned. In that case,
      // transparency information was transferred to the branch effect.
      (child2.f & BRANCH_EFFECT) !== 0 && (effect2.f & BLOCK_EFFECT) !== 0;
      pause_children(child2, transitions, transparent ? local : false);
    }
    child2 = sibling2;
  }
}
function resume_effect(effect2) {
  resume_children(effect2, true);
}
function resume_children(effect2, local) {
  if ((effect2.f & INERT) === 0) return;
  effect2.f ^= INERT;
  if ((effect2.f & CLEAN) === 0) {
    set_signal_status(effect2, DIRTY);
    Batch.ensure().schedule(effect2);
  }
  var child2 = effect2.first;
  while (child2 !== null) {
    var sibling2 = child2.next;
    var transparent = (child2.f & EFFECT_TRANSPARENT) !== 0 || (child2.f & BRANCH_EFFECT) !== 0;
    resume_children(child2, transparent ? local : false);
    child2 = sibling2;
  }
  var t = effect2.nodes && effect2.nodes.t;
  if (t !== null) {
    for (const transition2 of t) {
      if (transition2.is_global || local) {
        transition2.in();
      }
    }
  }
}
function move_effect(effect2, fragment) {
  if (!effect2.nodes) return;
  var node = effect2.nodes.start;
  var end = effect2.nodes.end;
  while (node !== null) {
    var next2 = node === end ? null : get_next_sibling(node);
    fragment.append(node);
    node = next2;
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/events.js
var event_symbol = /* @__PURE__ */ Symbol("events");
var all_registered_events = /* @__PURE__ */ new Set();
var root_event_handles = /* @__PURE__ */ new Set();
function create_event(event_name, dom, handler, options = {}) {
  function target_handler(event2) {
    if (!options.capture) {
      handle_event_propagation.call(dom, event2);
    }
    if (!event2.cancelBubble) {
      return without_reactive_context(() => {
        return handler?.call(this, event2);
      });
    }
  }
  if (event_name.startsWith("pointer") || event_name.startsWith("touch") || event_name === "wheel") {
    queue_micro_task(() => {
      dom.addEventListener(event_name, target_handler, options);
    });
  } else {
    dom.addEventListener(event_name, target_handler, options);
  }
  return target_handler;
}
function event(event_name, dom, handler, capture2, passive2) {
  var options = { capture: capture2, passive: passive2 };
  var target_handler = create_event(event_name, dom, handler, options);
  if (dom === document.body || // @ts-ignore
  dom === window || // @ts-ignore
  dom === document || // Firefox has quirky behavior, it can happen that we still get "canplay" events when the element is already removed
  dom instanceof HTMLMediaElement) {
    teardown(() => {
      dom.removeEventListener(event_name, target_handler, options);
    });
  }
}
function delegated(event_name, element2, handler) {
  (element2[event_symbol] ?? (element2[event_symbol] = {}))[event_name] = handler;
}
function delegate(events) {
  for (var i = 0; i < events.length; i++) {
    all_registered_events.add(events[i]);
  }
  for (var fn of root_event_handles) {
    fn(events);
  }
}
var last_propagated_event = null;
function handle_event_propagation(event2) {
  var handler_element = this;
  var owner_document = (
    /** @type {Node} */
    handler_element.ownerDocument
  );
  var event_name = event2.type;
  var path = event2.composedPath?.() || [];
  var current_target = (
    /** @type {null | Element} */
    path[0] || event2.target
  );
  last_propagated_event = event2;
  var path_idx = 0;
  var handled_at = last_propagated_event === event2 && event2[event_symbol];
  if (handled_at) {
    var at_idx = path.indexOf(handled_at);
    if (at_idx !== -1 && (handler_element === document || handler_element === /** @type {any} */
    window)) {
      event2[event_symbol] = handler_element;
      return;
    }
    var handler_idx = path.indexOf(handler_element);
    if (handler_idx === -1) {
      return;
    }
    if (at_idx <= handler_idx) {
      path_idx = at_idx;
    }
  }
  current_target = /** @type {Element} */
  path[path_idx] || event2.target;
  if (current_target === handler_element) return;
  define_property(event2, "currentTarget", {
    configurable: true,
    get() {
      return current_target || owner_document;
    }
  });
  var previous_reaction = active_reaction;
  var previous_effect = active_effect;
  set_active_reaction(null);
  set_active_effect(null);
  try {
    var throw_error;
    var other_errors = [];
    while (current_target !== null) {
      if (current_target === handler_element) break;
      try {
        var delegated2 = current_target[event_symbol]?.[event_name];
        if (delegated2 != null && (!/** @type {any} */
        current_target.disabled || // DOM could've been updated already by the time this is reached, so we check this as well
        // -> the target could not have been disabled because it emits the event in the first place
        event2.target === current_target)) {
          delegated2.call(current_target, event2);
        }
      } catch (error) {
        if (throw_error) {
          other_errors.push(error);
        } else {
          throw_error = error;
        }
      }
      if (event2.cancelBubble) break;
      path_idx++;
      current_target = path_idx < path.length ? (
        /** @type {Element} */
        path[path_idx]
      ) : null;
    }
    if (throw_error) {
      for (let error of other_errors) {
        queueMicrotask(() => {
          throw error;
        });
      }
      throw throw_error;
    }
  } finally {
    event2[event_symbol] = handler_element;
    delete event2.currentTarget;
    set_active_reaction(previous_reaction);
    set_active_effect(previous_effect);
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/reconciler.js
var policy = (
  // We gotta write it like this because after downleveling the pure comment may end up in the wrong location
  globalThis?.window?.trustedTypes && /* @__PURE__ */ globalThis.window.trustedTypes.createPolicy("svelte-trusted-html", {
    /** @param {string} html */
    createHTML: (html2) => {
      return html2;
    }
  })
);
function create_trusted_html(html2) {
  return (
    /** @type {string} */
    policy?.createHTML(html2) ?? html2
  );
}
function create_fragment_from_html(html2) {
  var elem = create_element("template");
  elem.innerHTML = create_trusted_html(html2.replaceAll("<!>", "<!---->"));
  return elem.content;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/template.js
function assign_nodes(start, end) {
  var effect2 = (
    /** @type {Effect} */
    active_effect
  );
  if (effect2.nodes === null) {
    effect2.nodes = { start, end, a: null, t: null };
  }
}
// @__NO_SIDE_EFFECTS__
function from_html(content, flags2) {
  var is_fragment = (flags2 & TEMPLATE_FRAGMENT) !== 0;
  var use_import_node = (flags2 & TEMPLATE_USE_IMPORT_NODE) !== 0;
  var node;
  var has_start = !content.startsWith("<!>");
  return () => {
    if (hydrating) {
      assign_nodes(hydrate_node, null);
      return hydrate_node;
    }
    if (node === void 0) {
      node = create_fragment_from_html(has_start ? content : "<!>" + content);
      if (!is_fragment) node = /** @type {TemplateNode} */
      get_first_child(node);
    }
    var clone = (
      /** @type {TemplateNode} */
      use_import_node || is_firefox ? document.importNode(node, true) : node.cloneNode(true)
    );
    if (is_fragment) {
      var start = (
        /** @type {TemplateNode} */
        get_first_child(clone)
      );
      var end = (
        /** @type {TemplateNode} */
        clone.lastChild
      );
      assign_nodes(start, end);
    } else {
      assign_nodes(clone, clone);
    }
    return clone;
  };
}
// @__NO_SIDE_EFFECTS__
function from_namespace(content, flags2, ns = "svg") {
  var has_start = !content.startsWith("<!>");
  var is_fragment = (flags2 & TEMPLATE_FRAGMENT) !== 0;
  var wrapped = `<${ns}>${has_start ? content : "<!>" + content}</${ns}>`;
  var node;
  return () => {
    if (hydrating) {
      assign_nodes(hydrate_node, null);
      return hydrate_node;
    }
    if (!node) {
      var fragment = (
        /** @type {DocumentFragment} */
        create_fragment_from_html(wrapped)
      );
      var root2 = (
        /** @type {Element} */
        get_first_child(fragment)
      );
      if (is_fragment) {
        node = document.createDocumentFragment();
        while (get_first_child(root2)) {
          node.appendChild(
            /** @type {TemplateNode} */
            get_first_child(root2)
          );
        }
      } else {
        node = /** @type {Element} */
        get_first_child(root2);
      }
    }
    var clone = (
      /** @type {TemplateNode} */
      node.cloneNode(true)
    );
    if (is_fragment) {
      var start = (
        /** @type {TemplateNode} */
        get_first_child(clone)
      );
      var end = (
        /** @type {TemplateNode} */
        clone.lastChild
      );
      assign_nodes(start, end);
    } else {
      assign_nodes(clone, clone);
    }
    return clone;
  };
}
// @__NO_SIDE_EFFECTS__
function from_svg(content, flags2) {
  return /* @__PURE__ */ from_namespace(content, flags2, "svg");
}
function text(value = "") {
  if (!hydrating) {
    var t = create_text(value + "");
    assign_nodes(t, t);
    return t;
  }
  var node = hydrate_node;
  if (node.nodeType !== TEXT_NODE) {
    node.before(node = create_text());
    set_hydrate_node(node);
  } else {
    merge_text_nodes(
      /** @type {Text} */
      node
    );
  }
  assign_nodes(node, node);
  return node;
}
function comment() {
  if (hydrating) {
    assign_nodes(hydrate_node, null);
    return hydrate_node;
  }
  var frag = document.createDocumentFragment();
  var start = document.createComment("");
  var anchor = create_text();
  frag.append(start, anchor);
  assign_nodes(start, anchor);
  return frag;
}
function append(anchor, dom) {
  if (hydrating) {
    var effect2 = (
      /** @type {Effect & { nodes: EffectNodes }} */
      active_effect
    );
    if ((effect2.f & REACTION_RAN) === 0 || effect2.nodes.end === null) {
      effect2.nodes.end = hydrate_node;
    }
    hydrate_next();
    return;
  }
  if (anchor === null) {
    return;
  }
  anchor.before(
    /** @type {Node} */
    dom
  );
}

// content/plugins/node_modules/svelte/src/utils.js
var DOM_BOOLEAN_ATTRIBUTES = [
  "allowfullscreen",
  "async",
  "autofocus",
  "autoplay",
  "checked",
  "controls",
  "default",
  "disabled",
  "formnovalidate",
  "indeterminate",
  "inert",
  "ismap",
  "loop",
  "multiple",
  "muted",
  "nomodule",
  "novalidate",
  "open",
  "playsinline",
  "readonly",
  "required",
  "reversed",
  "seamless",
  "selected",
  "webkitdirectory",
  "defer",
  "disablepictureinpicture",
  "disableremoteplayback"
];
var DOM_PROPERTIES = [
  ...DOM_BOOLEAN_ATTRIBUTES,
  "formNoValidate",
  "isMap",
  "noModule",
  "playsInline",
  "readOnly",
  "value",
  "volume",
  "defaultValue",
  "defaultChecked",
  "srcObject",
  "noValidate",
  "allowFullscreen",
  "disablePictureInPicture",
  "disableRemotePlayback"
];
var PASSIVE_EVENTS = ["touchstart", "touchmove"];
function is_passive_event(name) {
  return PASSIVE_EVENTS.includes(name);
}
var STATE_CREATION_RUNES = (
  /** @type {const} */
  [
    "$state",
    "$state.raw",
    "$derived",
    "$derived.by"
  ]
);
var RUNES = (
  /** @type {const} */
  [
    ...STATE_CREATION_RUNES,
    "$state.eager",
    "$state.snapshot",
    "$props",
    "$props.id",
    "$bindable",
    "$effect",
    "$effect.pre",
    "$effect.tracking",
    "$effect.root",
    "$effect.pending",
    "$inspect",
    "$inspect().with",
    "$inspect.trace",
    "$host"
  ]
);

// content/plugins/node_modules/svelte/src/internal/client/render.js
var should_intro = true;
function set_text(text2, value) {
  var _a2;
  var str = value == null ? "" : typeof value === "object" ? `${value}` : value;
  if (str !== /** @type {any} */
  (text2[_a2 = TEXT_CACHE] ?? (text2[_a2] = text2.nodeValue))) {
    text2[TEXT_CACHE] = str;
    text2.nodeValue = `${str}`;
  }
}
function mount(component2, options) {
  return _mount(component2, options);
}
function hydrate(component2, options) {
  init_operations();
  options.intro = options.intro ?? false;
  const target = options.target;
  const was_hydrating = hydrating;
  const previous_hydrate_node = hydrate_node;
  try {
    var anchor = get_first_child(target);
    while (anchor && (anchor.nodeType !== COMMENT_NODE || /** @type {Comment} */
    anchor.data !== HYDRATION_START)) {
      anchor = get_next_sibling(anchor);
    }
    if (!anchor) {
      throw HYDRATION_ERROR;
    }
    set_hydrating(true);
    set_hydrate_node(
      /** @type {Comment} */
      anchor
    );
    const instance = _mount(component2, { ...options, anchor });
    set_hydrating(false);
    return (
      /**  @type {Exports} */
      instance
    );
  } catch (error) {
    if (error instanceof Error && error.message.split("\n").some((line) => line.startsWith("https://svelte.dev/e/"))) {
      throw error;
    }
    if (error !== HYDRATION_ERROR) {
      console.warn("Failed to hydrate: ", error);
    }
    if (options.recover === false) {
      hydration_failed();
    }
    init_operations();
    clear_text_content(target);
    set_hydrating(false);
    return mount(component2, options);
  } finally {
    set_hydrating(was_hydrating);
    set_hydrate_node(previous_hydrate_node);
  }
}
var listeners = /* @__PURE__ */ new Map();
function _mount(Component, { target, anchor, props = {}, events, context, intro = true, transformError }) {
  init_operations();
  var component2 = void 0;
  var unmount2 = component_root(() => {
    var anchor_node = anchor ?? target.appendChild(create_text());
    boundary(
      /** @type {TemplateNode} */
      anchor_node,
      {
        pending: () => {
        }
      },
      (anchor_node2) => {
        push({});
        var ctx = (
          /** @type {ComponentContext} */
          component_context
        );
        if (context) ctx.c = context;
        if (events) {
          props.$$events = events;
        }
        if (hydrating) {
          assign_nodes(
            /** @type {TemplateNode} */
            anchor_node2,
            null
          );
        }
        should_intro = intro;
        component2 = Component(anchor_node2, props) || {};
        should_intro = true;
        if (hydrating) {
          active_effect.nodes.end = hydrate_node;
          if (hydrate_node === null || hydrate_node.nodeType !== COMMENT_NODE || /** @type {Comment} */
          hydrate_node.data !== HYDRATION_END) {
            hydration_mismatch();
            throw HYDRATION_ERROR;
          }
        }
        pop();
      },
      transformError
    );
    var registered_events = /* @__PURE__ */ new Set();
    var event_handle = (events2) => {
      for (var i = 0; i < events2.length; i++) {
        var event_name = events2[i];
        if (registered_events.has(event_name)) continue;
        registered_events.add(event_name);
        var passive2 = is_passive_event(event_name);
        for (const node of [target, document]) {
          var counts = listeners.get(node);
          if (counts === void 0) {
            counts = /* @__PURE__ */ new Map();
            listeners.set(node, counts);
          }
          var count = counts.get(event_name);
          if (count === void 0) {
            node.addEventListener(event_name, handle_event_propagation, { passive: passive2 });
            counts.set(event_name, 1);
          } else {
            counts.set(event_name, count + 1);
          }
        }
      }
    };
    event_handle(array_from(all_registered_events));
    root_event_handles.add(event_handle);
    return () => {
      for (var event_name of registered_events) {
        for (const node of [target, document]) {
          var counts = (
            /** @type {Map<string, number>} */
            listeners.get(node)
          );
          var count = (
            /** @type {number} */
            counts.get(event_name)
          );
          if (--count == 0) {
            node.removeEventListener(event_name, handle_event_propagation);
            counts.delete(event_name);
            if (counts.size === 0) {
              listeners.delete(node);
            }
          } else {
            counts.set(event_name, count);
          }
        }
      }
      root_event_handles.delete(event_handle);
      if (anchor_node !== anchor) {
        anchor_node.parentNode?.removeChild(anchor_node);
      }
    };
  });
  mounted_components.set(component2, unmount2);
  return component2;
}
var mounted_components = /* @__PURE__ */ new WeakMap();
function unmount(component2, options) {
  const fn = mounted_components.get(component2);
  if (fn) {
    mounted_components.delete(component2);
    return fn(options);
  }
  if (dev_fallback_default) {
    if (STATE_SYMBOL in component2) {
      state_proxy_unmount();
    } else {
      lifecycle_double_unmount();
    }
  }
  return Promise.resolve();
}

// content/plugins/node_modules/svelte/src/legacy/legacy-client.js
function createClassComponent(options) {
  return new Svelte4Component(options);
}
var _events, _instance;
var Svelte4Component = class {
  /**
   * @param {ComponentConstructorOptions & {
   *  component: any;
   * }} options
   */
  constructor(options) {
    /** @type {any} */
    __privateAdd(this, _events);
    /** @type {Record<string, any>} */
    __privateAdd(this, _instance);
    var sources = /* @__PURE__ */ new Map();
    var add_source = (key2, value) => {
      var s = mutable_source(value, false, false);
      sources.set(key2, s);
      return s;
    };
    const props = new Proxy(
      { ...options.props || {}, $$events: {} },
      {
        get(target, prop2) {
          return get(sources.get(prop2) ?? add_source(prop2, Reflect.get(target, prop2)));
        },
        has(target, prop2) {
          if (prop2 === LEGACY_PROPS) return true;
          get(sources.get(prop2) ?? add_source(prop2, Reflect.get(target, prop2)));
          return Reflect.has(target, prop2);
        },
        set(target, prop2, value) {
          set(sources.get(prop2) ?? add_source(prop2, value), value);
          return Reflect.set(target, prop2, value);
        }
      }
    );
    __privateSet(this, _instance, (options.hydrate ? hydrate : mount)(options.component, {
      target: options.target,
      anchor: options.anchor,
      props,
      context: options.context,
      intro: options.intro ?? false,
      recover: options.recover,
      transformError: options.transformError
    }));
    if (!async_mode_flag && (!options?.props?.$$host || options.sync === false)) {
      flushSync();
    }
    __privateSet(this, _events, props.$$events);
    for (const key2 of Object.keys(__privateGet(this, _instance))) {
      if (key2 === "$set" || key2 === "$destroy" || key2 === "$on") continue;
      define_property(this, key2, {
        get() {
          return __privateGet(this, _instance)[key2];
        },
        /** @param {any} value */
        set(value) {
          __privateGet(this, _instance)[key2] = value;
        },
        enumerable: true
      });
    }
    __privateGet(this, _instance).$set = /** @param {Record<string, any>} next */
    (next2) => {
      Object.assign(props, next2);
    };
    __privateGet(this, _instance).$destroy = () => {
      unmount(__privateGet(this, _instance));
    };
  }
  /** @param {Record<string, any>} props */
  $set(props) {
    __privateGet(this, _instance).$set(props);
  }
  /**
   * @param {string} event
   * @param {(...args: any[]) => any} callback
   * @returns {any}
   */
  $on(event2, callback) {
    __privateGet(this, _events)[event2] = __privateGet(this, _events)[event2] || [];
    const cb = (...args) => callback.call(this, ...args);
    __privateGet(this, _events)[event2].push(cb);
    return () => {
      __privateGet(this, _events)[event2] = __privateGet(this, _events)[event2].filter(
        /** @param {any} fn */
        (fn) => fn !== cb
      );
    };
  }
  $destroy() {
    __privateGet(this, _instance).$destroy();
  }
};
_events = new WeakMap();
_instance = new WeakMap();

// content/plugins/node_modules/svelte/src/version.js
var PUBLIC_VERSION = "5";

// content/plugins/node_modules/svelte/src/internal/disclose-version.js
var _a;
if (typeof window !== "undefined") {
  ((_a = window.__svelte ?? (window.__svelte = {})).v ?? (_a.v = /* @__PURE__ */ new Set())).add(PUBLIC_VERSION);
}

// content/plugins/node_modules/svelte/src/internal/client/dom/blocks/branches.js
var _batches, _onscreen, _offscreen, _outroing, _transition, _commit, _discard;
var BranchManager = class {
  /**
   * @param {TemplateNode} anchor
   * @param {boolean} transition
   */
  constructor(anchor, transition2 = true) {
    /** @type {TemplateNode} */
    __publicField(this, "anchor");
    /** @type {Map<Batch, Key>} */
    __privateAdd(this, _batches, /* @__PURE__ */ new Map());
    /**
     * Map of keys to effects that are currently rendered in the DOM.
     * These effects are visible and actively part of the document tree.
     * Example:
     * ```
     * {#if condition}
     * 	foo
     * {:else}
     * 	bar
     * {/if}
     * ```
     * Can result in the entries `true->Effect` and `false->Effect`
     * @type {Map<Key, Effect>}
     */
    __privateAdd(this, _onscreen, /* @__PURE__ */ new Map());
    /**
     * Similar to #onscreen with respect to the keys, but contains branches that are not yet
     * in the DOM, because their insertion is deferred.
     * @type {Map<Key, Branch>}
     */
    __privateAdd(this, _offscreen, /* @__PURE__ */ new Map());
    /**
     * Keys of effects that are currently outroing
     * @type {Set<Key>}
     */
    __privateAdd(this, _outroing, /* @__PURE__ */ new Set());
    /**
     * Whether to pause (i.e. outro) on change, or destroy immediately.
     * This is necessary for `<svelte:element>`
     */
    __privateAdd(this, _transition, true);
    /**
     * @param {Batch} batch
     */
    __privateAdd(this, _commit, (batch) => {
      if (!__privateGet(this, _batches).has(batch)) return;
      var key2 = (
        /** @type {Key} */
        __privateGet(this, _batches).get(batch)
      );
      var onscreen = __privateGet(this, _onscreen).get(key2);
      if (onscreen) {
        resume_effect(onscreen);
        __privateGet(this, _outroing).delete(key2);
      } else {
        var offscreen = __privateGet(this, _offscreen).get(key2);
        if (offscreen) {
          resume_effect(offscreen.effect);
          __privateGet(this, _onscreen).set(key2, offscreen.effect);
          __privateGet(this, _offscreen).delete(key2);
          if (dev_fallback_default) {
            offscreen.fragment.lastChild[HMR_ANCHOR] = this.anchor;
          }
          offscreen.fragment.lastChild.remove();
          this.anchor.before(offscreen.fragment);
          onscreen = offscreen.effect;
        }
      }
      for (const [b, k] of __privateGet(this, _batches)) {
        __privateGet(this, _batches).delete(b);
        if (b === batch) {
          break;
        }
        const offscreen2 = __privateGet(this, _offscreen).get(k);
        if (offscreen2) {
          destroy_effect(offscreen2.effect);
          __privateGet(this, _offscreen).delete(k);
        }
      }
      for (const [k, effect2] of __privateGet(this, _onscreen)) {
        if (k === key2 || __privateGet(this, _outroing).has(k)) continue;
        const on_destroy = () => {
          const keys = Array.from(__privateGet(this, _batches).values());
          if (keys.includes(k)) {
            var fragment = document.createDocumentFragment();
            move_effect(effect2, fragment);
            fragment.append(create_text());
            __privateGet(this, _offscreen).set(k, { effect: effect2, fragment });
          } else {
            destroy_effect(effect2);
          }
          __privateGet(this, _outroing).delete(k);
          __privateGet(this, _onscreen).delete(k);
        };
        if (__privateGet(this, _transition) || !onscreen) {
          __privateGet(this, _outroing).add(k);
          pause_effect(effect2, on_destroy, false);
        } else {
          on_destroy();
        }
      }
    });
    /**
     * @param {Batch} batch
     */
    __privateAdd(this, _discard, (batch) => {
      __privateGet(this, _batches).delete(batch);
      const keys = Array.from(__privateGet(this, _batches).values());
      for (const [k, branch2] of __privateGet(this, _offscreen)) {
        if (!keys.includes(k)) {
          destroy_effect(branch2.effect);
          __privateGet(this, _offscreen).delete(k);
        }
      }
    });
    this.anchor = anchor;
    __privateSet(this, _transition, transition2);
  }
  /**
   *
   * @param {any} key
   * @param {null | ((target: TemplateNode) => void)} fn
   */
  ensure(key2, fn) {
    var batch = (
      /** @type {Batch} */
      current_batch
    );
    var defer = should_defer_append();
    if (fn && !__privateGet(this, _onscreen).has(key2) && !__privateGet(this, _offscreen).has(key2)) {
      if (defer) {
        var fragment = document.createDocumentFragment();
        var target = create_text();
        fragment.append(target);
        __privateGet(this, _offscreen).set(key2, {
          effect: branch(() => fn(target)),
          fragment
        });
      } else {
        __privateGet(this, _onscreen).set(
          key2,
          branch(() => fn(this.anchor))
        );
      }
    }
    __privateGet(this, _batches).set(batch, key2);
    if (defer) {
      for (const [k, effect2] of __privateGet(this, _onscreen)) {
        if (k === key2) {
          batch.unskip_effect(effect2);
        } else {
          batch.skip_effect(effect2);
        }
      }
      for (const [k, branch2] of __privateGet(this, _offscreen)) {
        if (k === key2) {
          batch.unskip_effect(branch2.effect);
        } else {
          batch.skip_effect(branch2.effect);
        }
      }
      batch.oncommit(__privateGet(this, _commit));
      batch.ondiscard(__privateGet(this, _discard));
    } else {
      if (hydrating) {
        this.anchor = hydrate_node;
      }
      __privateGet(this, _commit).call(this, batch);
    }
  }
};
_batches = new WeakMap();
_onscreen = new WeakMap();
_offscreen = new WeakMap();
_outroing = new WeakMap();
_transition = new WeakMap();
_commit = new WeakMap();
_discard = new WeakMap();

// content/plugins/node_modules/svelte/src/index-client.js
if (dev_fallback_default) {
  let throw_rune_error = function(rune) {
    if (!(rune in globalThis)) {
      let value;
      Object.defineProperty(globalThis, rune, {
        configurable: true,
        // eslint-disable-next-line getter-return
        get: () => {
          if (value !== void 0) {
            return value;
          }
          rune_outside_svelte(rune);
        },
        set: (v) => {
          value = v;
        }
      });
    }
  };
  throw_rune_error("$state");
  throw_rune_error("$effect");
  throw_rune_error("$derived");
  throw_rune_error("$inspect");
  throw_rune_error("$props");
  throw_rune_error("$bindable");
}
function onMount(fn) {
  if (component_context === null) {
    lifecycle_outside_component("onMount");
  }
  if (legacy_mode_flag && component_context.l !== null) {
    init_update_callbacks(component_context).m.push(fn);
  } else {
    user_effect(() => {
      const cleanup = untrack(fn);
      if (typeof cleanup === "function") return (
        /** @type {() => void} */
        cleanup
      );
    });
  }
}
function init_update_callbacks(context) {
  var l = (
    /** @type {ComponentContextLegacy} */
    context.l
  );
  return l.u ?? (l.u = { a: [], b: [], m: [] });
}

// content/plugins/node_modules/svelte/src/internal/client/dev/css.js
var all_styles = /* @__PURE__ */ new Map();
function register_style(hash2, style) {
  var styles = all_styles.get(hash2);
  if (!styles) {
    styles = /* @__PURE__ */ new Set();
    all_styles.set(hash2, styles);
  }
  styles.add(style);
}

// content/plugins/node_modules/svelte/src/internal/client/dom/blocks/if.js
function if_block(node, fn, elseif = false) {
  var marker;
  if (hydrating) {
    marker = hydrate_node;
    hydrate_next();
  }
  var branches = new BranchManager(node);
  var flags2 = elseif ? EFFECT_TRANSPARENT : 0;
  function update_branch(key2, fn2) {
    if (hydrating) {
      var data = read_hydration_instruction(
        /** @type {TemplateNode} */
        marker
      );
      if (key2 !== parseInt(data.substring(1))) {
        var anchor = skip_nodes();
        set_hydrate_node(anchor);
        branches.anchor = anchor;
        set_hydrating(false);
        branches.ensure(key2, fn2);
        set_hydrating(true);
        return;
      }
    }
    branches.ensure(key2, fn2);
  }
  block(() => {
    var has_branch = false;
    fn((fn2, key2 = 0) => {
      has_branch = true;
      update_branch(key2, fn2);
    });
    if (!has_branch) {
      update_branch(-1, null);
    }
  }, flags2);
}

// content/plugins/node_modules/svelte/src/internal/client/dom/blocks/each.js
function index(_, i) {
  return i;
}
function pause_effects(state2, to_destroy, controlled_anchor) {
  var transitions = [];
  var length = to_destroy.length;
  var group;
  var remaining = to_destroy.length;
  for (var i = 0; i < length; i++) {
    let effect2 = to_destroy[i];
    pause_effect(
      effect2,
      () => {
        if (group) {
          group.pending.delete(effect2);
          group.done.add(effect2);
          if (group.pending.size === 0) {
            var groups = (
              /** @type {Set<EachOutroGroup>} */
              state2.outrogroups
            );
            destroy_effects(state2, array_from(group.done));
            groups.delete(group);
            if (groups.size === 0) {
              state2.outrogroups = null;
            }
          }
        } else {
          remaining -= 1;
        }
      },
      false
    );
  }
  if (remaining === 0) {
    var fast_path = transitions.length === 0 && controlled_anchor !== null;
    if (fast_path) {
      var anchor = (
        /** @type {Element} */
        controlled_anchor
      );
      var parent_node = (
        /** @type {Element} */
        anchor.parentNode
      );
      clear_text_content(parent_node);
      parent_node.append(anchor);
      state2.items.clear();
    }
    destroy_effects(state2, to_destroy, !fast_path);
  } else {
    group = {
      pending: new Set(to_destroy),
      done: /* @__PURE__ */ new Set()
    };
    (state2.outrogroups ?? (state2.outrogroups = /* @__PURE__ */ new Set())).add(group);
  }
}
function destroy_effects(state2, to_destroy, remove_dom = true) {
  var preserved_effects;
  if (state2.pending.size > 0) {
    preserved_effects = /* @__PURE__ */ new Set();
    for (const keys of state2.pending.values()) {
      for (const key2 of keys) {
        preserved_effects.add(
          /** @type {EachItem} */
          state2.items.get(key2).e
        );
      }
    }
  }
  for (var i = 0; i < to_destroy.length; i++) {
    var e = to_destroy[i];
    if (preserved_effects?.has(e)) {
      e.f |= EFFECT_OFFSCREEN;
      const fragment = document.createDocumentFragment();
      move_effect(e, fragment);
    } else {
      destroy_effect(to_destroy[i], remove_dom);
    }
  }
}
var offscreen_anchor;
function each(node, flags2, get_collection, get_key, render_fn2, fallback_fn = null) {
  var anchor = node;
  var items = /* @__PURE__ */ new Map();
  var is_controlled = (flags2 & EACH_IS_CONTROLLED) !== 0;
  if (is_controlled) {
    var parent_node = (
      /** @type {Element} */
      node
    );
    anchor = hydrating ? set_hydrate_node(get_first_child(parent_node)) : parent_node.appendChild(create_text());
  }
  if (hydrating) {
    hydrate_next();
  }
  var fallback2 = null;
  var each_array = derived_safe_equal(() => {
    var collection = get_collection();
    return (
      /** @type {V[]} */
      is_array(collection) ? collection : collection == null ? [] : array_from(collection)
    );
  });
  if (dev_fallback_default) {
    tag(each_array, "{#each ...}");
  }
  var array;
  var pending2 = /* @__PURE__ */ new Map();
  var first_run = true;
  function commit(batch) {
    if ((state2.effect.f & DESTROYED) !== 0) {
      return;
    }
    state2.pending.delete(batch);
    state2.fallback = fallback2;
    reconcile(state2, array, anchor, flags2, get_key);
    if (fallback2 !== null) {
      if (array.length === 0) {
        if ((fallback2.f & EFFECT_OFFSCREEN) === 0) {
          resume_effect(fallback2);
        } else {
          fallback2.f ^= EFFECT_OFFSCREEN;
          move(fallback2, null, anchor);
        }
      } else {
        pause_effect(fallback2, () => {
          fallback2 = null;
        });
      }
    }
  }
  function discard(batch) {
    state2.pending.delete(batch);
  }
  var effect2 = block(() => {
    array = /** @type {V[]} */
    get(each_array);
    var length = array.length;
    let mismatch = false;
    if (hydrating) {
      var is_else = read_hydration_instruction(anchor) === HYDRATION_START_ELSE;
      if (is_else !== (length === 0)) {
        anchor = skip_nodes();
        set_hydrate_node(anchor);
        set_hydrating(false);
        mismatch = true;
      }
    }
    var keys = /* @__PURE__ */ new Set();
    var batch = (
      /** @type {Batch} */
      current_batch
    );
    var defer = should_defer_append();
    for (var index2 = 0; index2 < length; index2 += 1) {
      if (hydrating && hydrate_node.nodeType === COMMENT_NODE && /** @type {Comment} */
      hydrate_node.data === HYDRATION_END) {
        anchor = /** @type {Comment} */
        hydrate_node;
        mismatch = true;
        set_hydrating(false);
      }
      var value = array[index2];
      var key2 = get_key(value, index2);
      if (dev_fallback_default) {
        var key_again = get_key(value, index2);
        if (key2 !== key_again) {
          each_key_volatile(String(index2), String(key2), String(key_again));
        }
      }
      var item = first_run ? null : items.get(key2);
      if (item) {
        if (item.v) internal_set(item.v, value);
        if (item.i) internal_set(item.i, index2);
        if (defer) {
          batch.unskip_effect(item.e);
        }
      } else {
        item = create_item(
          items,
          first_run ? anchor : offscreen_anchor ?? (offscreen_anchor = create_text()),
          value,
          key2,
          index2,
          render_fn2,
          flags2,
          get_collection
        );
        if (!first_run) {
          item.e.f |= EFFECT_OFFSCREEN;
        }
        items.set(key2, item);
      }
      keys.add(key2);
    }
    if (length === 0 && fallback_fn && !fallback2) {
      if (first_run) {
        fallback2 = branch(() => fallback_fn(anchor));
      } else {
        fallback2 = branch(() => fallback_fn(offscreen_anchor ?? (offscreen_anchor = create_text())));
        fallback2.f |= EFFECT_OFFSCREEN;
      }
    }
    if (length > keys.size) {
      if (dev_fallback_default) {
        validate_each_keys(array, get_key);
      } else {
        each_key_duplicate("", "", "");
      }
    }
    if (hydrating && length > 0) {
      set_hydrate_node(skip_nodes());
    }
    if (!first_run) {
      pending2.set(batch, keys);
      if (defer) {
        for (const [key3, item2] of items) {
          if (!keys.has(key3)) {
            batch.skip_effect(item2.e);
          }
        }
        batch.oncommit(commit);
        batch.ondiscard(discard);
      } else {
        commit(batch);
      }
    }
    if (mismatch) {
      set_hydrating(true);
    }
    get(each_array);
  });
  var state2 = { effect: effect2, flags: flags2, items, pending: pending2, outrogroups: null, fallback: fallback2 };
  first_run = false;
  if (hydrating) {
    anchor = hydrate_node;
  }
}
function skip_to_branch(effect2) {
  while (effect2 !== null && (effect2.f & BRANCH_EFFECT) === 0) {
    effect2 = effect2.next;
  }
  return effect2;
}
function reconcile(state2, array, anchor, flags2, get_key) {
  var is_animated = (flags2 & EACH_IS_ANIMATED) !== 0;
  var length = array.length;
  var items = state2.items;
  var current = skip_to_branch(state2.effect.first);
  var seen;
  var prev = null;
  var to_animate;
  var matched = [];
  var stashed = [];
  var value;
  var key2;
  var effect2;
  var i;
  if (is_animated) {
    for (i = 0; i < length; i += 1) {
      value = array[i];
      key2 = get_key(value, i);
      effect2 = /** @type {EachItem} */
      items.get(key2).e;
      if ((effect2.f & EFFECT_OFFSCREEN) === 0) {
        effect2.nodes?.a?.measure();
        (to_animate ?? (to_animate = /* @__PURE__ */ new Set())).add(effect2);
      }
    }
  }
  for (i = 0; i < length; i += 1) {
    value = array[i];
    key2 = get_key(value, i);
    effect2 = /** @type {EachItem} */
    items.get(key2).e;
    if (state2.outrogroups !== null) {
      for (const group of state2.outrogroups) {
        group.pending.delete(effect2);
        group.done.delete(effect2);
      }
    }
    if ((effect2.f & INERT) !== 0) {
      resume_effect(effect2);
      if (is_animated) {
        effect2.nodes?.a?.unfix();
        (to_animate ?? (to_animate = /* @__PURE__ */ new Set())).delete(effect2);
      }
    }
    if ((effect2.f & EFFECT_OFFSCREEN) !== 0) {
      effect2.f ^= EFFECT_OFFSCREEN;
      if (effect2 === current) {
        move(effect2, null, anchor);
      } else {
        var next2 = prev ? prev.next : current;
        if (effect2 === state2.effect.last) {
          state2.effect.last = effect2.prev;
        }
        if (effect2.prev) effect2.prev.next = effect2.next;
        if (effect2.next) effect2.next.prev = effect2.prev;
        link(state2, prev, effect2);
        link(state2, effect2, next2);
        move(effect2, next2, anchor);
        prev = effect2;
        matched = [];
        stashed = [];
        current = skip_to_branch(prev.next);
        continue;
      }
    }
    if (effect2 !== current) {
      if (seen !== void 0 && seen.has(effect2)) {
        if (matched.length < stashed.length) {
          var start = stashed[0];
          var j;
          prev = start.prev;
          var a = matched[0];
          var b = matched[matched.length - 1];
          for (j = 0; j < matched.length; j += 1) {
            move(matched[j], start, anchor);
          }
          for (j = 0; j < stashed.length; j += 1) {
            seen.delete(stashed[j]);
          }
          link(state2, a.prev, b.next);
          link(state2, prev, a);
          link(state2, b, start);
          current = start;
          prev = b;
          i -= 1;
          matched = [];
          stashed = [];
        } else {
          seen.delete(effect2);
          move(effect2, current, anchor);
          link(state2, effect2.prev, effect2.next);
          link(state2, effect2, prev === null ? state2.effect.first : prev.next);
          link(state2, prev, effect2);
          prev = effect2;
        }
        continue;
      }
      matched = [];
      stashed = [];
      while (current !== null && current !== effect2) {
        (seen ?? (seen = /* @__PURE__ */ new Set())).add(current);
        stashed.push(current);
        current = skip_to_branch(current.next);
      }
      if (current === null) {
        continue;
      }
    }
    if ((effect2.f & EFFECT_OFFSCREEN) === 0) {
      matched.push(effect2);
    }
    prev = effect2;
    current = skip_to_branch(effect2.next);
  }
  if (state2.outrogroups !== null) {
    for (const group of state2.outrogroups) {
      if (group.pending.size === 0) {
        destroy_effects(state2, array_from(group.done));
        state2.outrogroups?.delete(group);
      }
    }
    if (state2.outrogroups.size === 0) {
      state2.outrogroups = null;
    }
  }
  if (current !== null || seen !== void 0) {
    var to_destroy = [];
    if (seen !== void 0) {
      for (effect2 of seen) {
        if ((effect2.f & INERT) === 0) {
          to_destroy.push(effect2);
        }
      }
    }
    while (current !== null) {
      if ((current.f & INERT) === 0 && current !== state2.fallback) {
        to_destroy.push(current);
      }
      current = skip_to_branch(current.next);
    }
    var destroy_length = to_destroy.length;
    if (destroy_length > 0) {
      var controlled_anchor = (flags2 & EACH_IS_CONTROLLED) !== 0 && length === 0 ? anchor : null;
      if (is_animated) {
        for (i = 0; i < destroy_length; i += 1) {
          to_destroy[i].nodes?.a?.measure();
        }
        for (i = 0; i < destroy_length; i += 1) {
          to_destroy[i].nodes?.a?.fix();
        }
      }
      pause_effects(state2, to_destroy, controlled_anchor);
    }
  }
  if (is_animated) {
    queue_micro_task(() => {
      if (to_animate === void 0) return;
      for (effect2 of to_animate) {
        effect2.nodes?.a?.apply();
      }
    });
  }
}
function create_item(items, anchor, value, key2, index2, render_fn2, flags2, get_collection) {
  var v = (flags2 & EACH_ITEM_REACTIVE) !== 0 ? (flags2 & EACH_ITEM_IMMUTABLE) === 0 ? mutable_source(value, false, false) : source(value) : null;
  var i = (flags2 & EACH_INDEX_REACTIVE) !== 0 ? source(index2) : null;
  if (dev_fallback_default && v) {
    v.trace = () => {
      get_collection()[i?.v ?? index2];
    };
  }
  return {
    v,
    i,
    e: branch(() => {
      render_fn2(anchor, v ?? value, i ?? index2, get_collection);
      return () => {
        items.delete(key2);
      };
    })
  };
}
function move(effect2, next2, anchor) {
  if (!effect2.nodes) return;
  var node = effect2.nodes.start;
  var end = effect2.nodes.end;
  var dest = next2 && (next2.f & EFFECT_OFFSCREEN) === 0 ? (
    /** @type {EffectNodes} */
    next2.nodes.start
  ) : anchor;
  while (node !== null) {
    var next_node = (
      /** @type {TemplateNode} */
      get_next_sibling(node)
    );
    dest.before(node);
    if (node === end) {
      return;
    }
    node = next_node;
  }
}
function link(state2, prev, next2) {
  if (prev === null) {
    state2.effect.first = next2;
  } else {
    prev.next = next2;
  }
  if (next2 === null) {
    state2.effect.last = prev;
  } else {
    next2.prev = prev;
  }
}
function validate_each_keys(array, key_fn) {
  const keys = /* @__PURE__ */ new Map();
  const length = array.length;
  for (let i = 0; i < length; i++) {
    const key2 = key_fn(array[i], i);
    if (keys.has(key2)) {
      const a = String(keys.get(key2));
      const b = String(i);
      let k = String(key2);
      if (k.startsWith("[object ")) k = null;
      each_key_duplicate(a, b, k);
    }
    keys.set(key2, i);
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/css.js
function append_styles(anchor, css) {
  effect(() => {
    var root2 = anchor.getRootNode();
    var target = (
      /** @type {ShadowRoot} */
      root2.host ? (
        /** @type {ShadowRoot} */
        root2
      ) : (
        /** @type {Document} */
        root2.head ?? /** @type {Document} */
        root2.ownerDocument.head
      )
    );
    if (!target.querySelector("#" + css.hash)) {
      const style = create_element("style");
      style.id = css.hash;
      style.textContent = css.code;
      target.appendChild(style);
      if (dev_fallback_default) {
        register_style(css.hash, style);
      }
    }
  });
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/actions.js
function action(dom, action2, get_value) {
  effect(() => {
    var payload = untrack(() => action2(dom, get_value?.()) || {});
    if (get_value && payload?.update) {
      var inited = false;
      var prev = (
        /** @type {any} */
        {}
      );
      render_effect(() => {
        var value = get_value();
        deep_read_state(value);
        if (inited && safe_not_equal(prev, value)) {
          prev = value;
          payload.update(value);
        }
      });
      inited = true;
    }
    if (payload?.destroy) {
      return () => (
        /** @type {Function} */
        payload.destroy()
      );
    }
  });
}

// content/plugins/node_modules/svelte/src/internal/shared/attributes.js
var whitespace = [..." 	\n\r\f\xA0\v\uFEFF"];
function to_class(value, hash2, directives) {
  var classname = value == null ? "" : "" + value;
  if (hash2) {
    classname = classname ? classname + " " + hash2 : hash2;
  }
  if (directives) {
    for (var key2 of Object.keys(directives)) {
      if (directives[key2]) {
        classname = classname ? classname + " " + key2 : key2;
      } else if (classname.length) {
        var len = key2.length;
        var a = 0;
        while ((a = classname.indexOf(key2, a)) >= 0) {
          var b = a + len;
          if ((a === 0 || whitespace.includes(classname[a - 1])) && (b === classname.length || whitespace.includes(classname[b]))) {
            classname = (a === 0 ? "" : classname.substring(0, a)) + classname.substring(b + 1);
          } else {
            a = b;
          }
        }
      }
    }
  }
  return classname === "" ? null : classname;
}
function append_styles2(styles, important = false) {
  var separator = important ? " !important;" : ";";
  var css = "";
  for (var key2 of Object.keys(styles)) {
    var value = styles[key2];
    if (value != null && value !== "") {
      css += " " + key2 + ": " + value + separator;
    }
  }
  return css;
}
function to_css_name(name) {
  if (name[0] !== "-" || name[1] !== "-") {
    return name.toLowerCase();
  }
  return name;
}
function to_style(value, styles) {
  if (styles) {
    var new_style = "";
    var normal_styles;
    var important_styles;
    if (Array.isArray(styles)) {
      normal_styles = styles[0];
      important_styles = styles[1];
    } else {
      normal_styles = styles;
    }
    if (value) {
      value = String(value).replaceAll(/\s*\/\*.*?\*\/\s*/g, "").trim();
      var in_str = false;
      var in_apo = 0;
      var in_comment = false;
      var reserved_names = [];
      if (normal_styles) {
        reserved_names.push(...Object.keys(normal_styles).map(to_css_name));
      }
      if (important_styles) {
        reserved_names.push(...Object.keys(important_styles).map(to_css_name));
      }
      var start_index = 0;
      var name_index = -1;
      const len = value.length;
      for (var i = 0; i < len; i++) {
        var c = value[i];
        if (in_comment) {
          if (c === "/" && value[i - 1] === "*") {
            in_comment = false;
          }
        } else if (in_str) {
          if (in_str === c) {
            in_str = false;
          }
        } else if (c === "/" && value[i + 1] === "*") {
          in_comment = true;
        } else if (c === '"' || c === "'") {
          in_str = c;
        } else if (c === "(") {
          in_apo++;
        } else if (c === ")") {
          in_apo--;
        }
        if (!in_comment && in_str === false && in_apo === 0) {
          if (c === ":" && name_index === -1) {
            name_index = i;
          } else if (c === ";" || i === len - 1) {
            if (name_index !== -1) {
              var name = to_css_name(value.substring(start_index, name_index).trim());
              if (!reserved_names.includes(name)) {
                if (c !== ";") {
                  i++;
                }
                var property = value.substring(start_index, i).trim();
                new_style += " " + property + ";";
              }
            }
            start_index = i + 1;
            name_index = -1;
          }
        }
      }
    }
    if (normal_styles) {
      new_style += append_styles2(normal_styles);
    }
    if (important_styles) {
      new_style += append_styles2(important_styles, true);
    }
    new_style = new_style.trim();
    return new_style === "" ? null : new_style;
  }
  return value == null ? null : String(value);
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/class.js
function set_class(dom, is_html, value, hash2, prev_classes, next_classes) {
  var prev = (
    /** @type {any} */
    dom[CLASS_CACHE]
  );
  if (hydrating || prev !== value || prev === void 0) {
    var next_class_name = to_class(value, hash2, next_classes);
    if (!hydrating || next_class_name !== dom.getAttribute("class")) {
      if (next_class_name == null) {
        dom.removeAttribute("class");
      } else if (is_html) {
        dom.className = next_class_name;
      } else {
        dom.setAttribute("class", next_class_name);
      }
    }
    dom[CLASS_CACHE] = value;
  } else if (next_classes && prev_classes !== next_classes) {
    for (var key2 in next_classes) {
      var is_present = !!next_classes[key2];
      if (prev_classes == null || is_present !== !!prev_classes[key2]) {
        dom.classList.toggle(key2, is_present);
      }
    }
  }
  return next_classes;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/style.js
function update_styles(dom, prev = {}, next2, priority) {
  for (var key2 in next2) {
    var value = next2[key2];
    if (prev[key2] !== value) {
      if (next2[key2] == null) {
        dom.style.removeProperty(key2);
      } else {
        dom.style.setProperty(key2, value, priority);
      }
    }
  }
}
function set_style(dom, value, prev_styles, next_styles) {
  var prev = (
    /** @type {any} */
    dom[STYLE_CACHE]
  );
  if (hydrating || prev !== value) {
    var next_style_attr = to_style(value, next_styles);
    if (!hydrating || next_style_attr !== dom.getAttribute("style")) {
      if (next_style_attr == null) {
        dom.removeAttribute("style");
      } else {
        dom.style.cssText = next_style_attr;
      }
    }
    dom[STYLE_CACHE] = value;
  } else if (next_styles) {
    if (Array.isArray(next_styles)) {
      update_styles(dom, prev_styles?.[0], next_styles[0]);
      update_styles(dom, prev_styles?.[1], next_styles[1], "important");
    } else {
      update_styles(dom, prev_styles, next_styles);
    }
  }
  return next_styles;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/bindings/select.js
function select_option(select, value, mounting = false) {
  if (select.multiple) {
    if (value == void 0) {
      return;
    }
    if (!is_array(value)) {
      return select_multiple_invalid_value();
    }
    for (var option of select.options) {
      option.selected = value.includes(get_option_value(option));
    }
    return;
  }
  for (option of select.options) {
    var option_value = get_option_value(option);
    if (is(option_value, value)) {
      option.selected = true;
      return;
    }
  }
  if (!mounting || value !== void 0) {
    select.selectedIndex = -1;
  }
}
function init_select(select) {
  var observer = new MutationObserver(() => {
    select_option(select, select.__value);
  });
  observer.observe(select, {
    // Listen to option element changes
    childList: true,
    subtree: true,
    // because of <optgroup>
    // Listen to option element value attribute changes
    // (doesn't get notified of select value changes,
    // because that property is not reflected as an attribute)
    attributes: true,
    attributeFilter: ["value"]
  });
  teardown(() => {
    observer.disconnect();
  });
}
function get_option_value(option) {
  if ("__value" in option) {
    return option.__value;
  } else {
    return option.value;
  }
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/attributes.js
var IS_CUSTOM_ELEMENT = /* @__PURE__ */ Symbol("is custom element");
var IS_HTML = /* @__PURE__ */ Symbol("is html");
var LINK_TAG = IS_XHTML ? "link" : "LINK";
var PROGRESS_TAG = IS_XHTML ? "progress" : "PROGRESS";
function remove_input_defaults(input) {
  if (!hydrating) return;
  var already_removed = false;
  var remove_defaults = () => {
    if (already_removed) return;
    already_removed = true;
    if (input.hasAttribute("value")) {
      var value = input.value;
      set_attribute2(input, "value", null);
      input.value = value;
    }
    if (input.hasAttribute("checked")) {
      var checked = input.checked;
      set_attribute2(input, "checked", null);
      input.checked = checked;
    }
  };
  input[FORM_RESET_HANDLER] = remove_defaults;
  queue_micro_task(remove_defaults);
  add_form_reset_listener();
}
function set_value(element2, value) {
  var attributes = get_attributes(element2);
  if (attributes.value === (attributes.value = // treat null and undefined the same for the initial value
  value ?? void 0) || // @ts-expect-error
  // `progress` elements always need their value set when it's `0`
  element2.value === value && (value !== 0 || element2.nodeName !== PROGRESS_TAG)) {
    return;
  }
  element2.value = value ?? "";
}
function set_checked(element2, checked) {
  var attributes = get_attributes(element2);
  if (attributes.checked === (attributes.checked = // treat null and undefined the same for the initial value
  checked ?? void 0)) {
    return;
  }
  element2.checked = checked;
}
function set_attribute2(element2, attribute, value, skip_warning) {
  var attributes = get_attributes(element2);
  if (hydrating) {
    attributes[attribute] = element2.getAttribute(attribute);
    if (attribute === "src" || attribute === "srcset" || attribute === "href" && element2.nodeName === LINK_TAG) {
      if (!skip_warning) {
        check_src_in_dev_hydration(element2, attribute, value ?? "");
      }
      return;
    }
  }
  if (attributes[attribute] === (attributes[attribute] = value)) return;
  if (attribute === "loading") {
    element2[LOADING_ATTR_SYMBOL] = value;
  }
  if (value == null) {
    element2.removeAttribute(attribute);
  } else if (typeof value !== "string" && get_setters(element2).includes(attribute)) {
    element2[attribute] = value;
  } else {
    element2.setAttribute(attribute, value);
  }
}
function get_attributes(element2) {
  var _a2;
  return (
    /** @type {Record<string | symbol, unknown>} **/
    /** @type {any} */
    element2[_a2 = ATTRIBUTES_CACHE] ?? (element2[_a2] = {
      [IS_CUSTOM_ELEMENT]: element2.nodeName.includes("-"),
      [IS_HTML]: element2.namespaceURI === NAMESPACE_HTML
    })
  );
}
var setters_cache = /* @__PURE__ */ new Map();
function get_setters(element2) {
  var cache_key = element2.getAttribute("is") || element2.nodeName;
  var setters = setters_cache.get(cache_key);
  if (setters) return setters;
  setters_cache.set(cache_key, setters = []);
  var descriptors;
  var proto = element2;
  var element_proto = Element.prototype;
  while (element_proto !== proto) {
    descriptors = get_descriptors(proto);
    for (var key2 in descriptors) {
      if (descriptors[key2].set && // better safe than sorry, we don't want spread attributes to mess with HTML content
      key2 !== "innerHTML" && key2 !== "textContent" && key2 !== "innerText") {
        setters.push(key2);
      }
    }
    proto = get_prototype_of(proto);
  }
  return setters;
}
function check_src_in_dev_hydration(element2, attribute, value) {
  if (!dev_fallback_default) return;
  if (attribute === "srcset" && srcset_url_equal(element2, value)) return;
  if (src_url_equal(element2.getAttribute(attribute) ?? "", value)) return;
  hydration_attribute_changed(
    attribute,
    element2.outerHTML.replace(element2.innerHTML, element2.innerHTML && "..."),
    String(value)
  );
}
function src_url_equal(element_src, url) {
  if (element_src === url) return true;
  return new URL(element_src, document.baseURI).href === new URL(url, document.baseURI).href;
}
function split_srcset(srcset) {
  return srcset.split(",").map((src) => src.trim().split(" ").filter(Boolean));
}
function srcset_url_equal(element2, srcset) {
  var element_urls = split_srcset(element2.srcset);
  var urls = split_srcset(srcset);
  return urls.length === element_urls.length && urls.every(
    ([url, width], i) => width === element_urls[i][1] && // We need to test both ways because Vite will create an a full URL with
    // `new URL(asset, import.meta.url).href` for the client when `base: './'`, and the
    // relative URLs inside srcset are not automatically resolved to absolute URLs by
    // browsers (in contrast to img.src). This means both SSR and DOM code could
    // contain relative or absolute URLs.
    (src_url_equal(element_urls[i][0], url) || src_url_equal(url, element_urls[i][0]))
  );
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/bindings/input.js
function bind_value(input, get3, set2 = get3) {
  var batches = /* @__PURE__ */ new WeakSet();
  listen_to_event_and_reset_event(input, "input", async (is_reset) => {
    if (dev_fallback_default && input.type === "checkbox") {
      bind_invalid_checkbox_value();
    }
    var value = is_reset ? input.defaultValue : input.value;
    value = is_numberlike_input(input) ? to_number(value) : value;
    set2(value);
    if (current_batch !== null) {
      batches.add(current_batch);
    }
    await tick();
    if (value !== (value = get3())) {
      var start = input.selectionStart;
      var end = input.selectionEnd;
      var length = input.value.length;
      input.value = value ?? "";
      if (end !== null) {
        var new_length = input.value.length;
        if (start === end && end === length && new_length > length) {
          input.selectionStart = new_length;
          input.selectionEnd = new_length;
        } else {
          input.selectionStart = start;
          input.selectionEnd = Math.min(end, new_length);
        }
      }
    }
  });
  if (
    // If we are hydrating and the value has since changed,
    // then use the updated value from the input instead.
    hydrating && input.defaultValue !== input.value || // If defaultValue is set, then value == defaultValue
    // TODO Svelte 6: remove input.value check and set to empty string?
    untrack(get3) == null && input.value
  ) {
    set2(is_numberlike_input(input) ? to_number(input.value) : input.value);
    if (current_batch !== null) {
      batches.add(current_batch);
    }
  }
  render_effect(() => {
    if (dev_fallback_default && input.type === "checkbox") {
      bind_invalid_checkbox_value();
    }
    var value = get3();
    if (input === document.activeElement) {
      var batch = (
        /** @type {Batch} */
        async_mode_flag ? previous_batch : current_batch
      );
      if (batches.has(batch)) {
        return;
      }
    }
    if (is_numberlike_input(input) && value === to_number(input.value)) {
      return;
    }
    if (input.type === "date" && !value && !input.value) {
      return;
    }
    if (value !== input.value) {
      input.value = value ?? "";
    }
  });
}
function bind_checked(input, get3, set2 = get3) {
  listen_to_event_and_reset_event(input, "change", (is_reset) => {
    var value = is_reset ? input.defaultChecked : input.checked;
    set2(value);
  });
  if (
    // If we are hydrating and the value has since changed,
    // then use the update value from the input instead.
    hydrating && input.defaultChecked !== input.checked || // If defaultChecked is set, then checked == defaultChecked
    untrack(get3) == null
  ) {
    set2(input.checked);
  }
  render_effect(() => {
    var value = get3();
    input.checked = Boolean(value);
  });
}
function is_numberlike_input(input) {
  var type = input.type;
  return type === "number" || type === "range";
}
function to_number(value) {
  return value === "" ? null : +value;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/bindings/this.js
function is_bound_this(bound_value, element_or_component) {
  return bound_value === element_or_component || bound_value?.[STATE_SYMBOL] === element_or_component;
}
function bind_this(element_or_component = {}, update2, get_value, get_parts) {
  var component_effect = (
    /** @type {ComponentContext} */
    component_context.r
  );
  var parent = (
    /** @type {Effect} */
    active_effect
  );
  effect(() => {
    var old_parts;
    var parts;
    render_effect(() => {
      old_parts = parts;
      parts = get_parts?.() || [];
      untrack(() => {
        if (!is_bound_this(get_value(...parts), element_or_component)) {
          update2(element_or_component, ...parts);
          if (old_parts && is_bound_this(get_value(...old_parts), element_or_component)) {
            update2(null, ...old_parts);
          }
        }
      });
    });
    return () => {
      let p = parent;
      while (p !== component_effect && p.parent !== null && p.parent.f & DESTROYING) {
        p = p.parent;
      }
      const teardown2 = () => {
        if (parts && is_bound_this(get_value(...parts), element_or_component)) {
          update2(null, ...parts);
        }
      };
      const original_teardown = p.teardown;
      p.teardown = () => {
        teardown2();
        original_teardown?.();
      };
    };
  });
  return element_or_component;
}

// content/plugins/node_modules/svelte/src/internal/client/dom/legacy/misc.js
function add_legacy_event_listener($$props, event_name, event_callback) {
  var _a2;
  $$props.$$events || ($$props.$$events = {});
  (_a2 = $$props.$$events)[event_name] || (_a2[event_name] = []);
  $$props.$$events[event_name].push(event_callback);
}
function update_legacy_props($$new_props) {
  for (var key2 in $$new_props) {
    if (key2 in this) {
      this[key2] = $$new_props[key2];
    }
  }
}

// content/plugins/node_modules/svelte/src/internal/client/reactivity/props.js
function prop(props, key2, flags2, fallback2) {
  var runes = !legacy_mode_flag || (flags2 & PROPS_IS_RUNES) !== 0;
  var bindable = (flags2 & PROPS_IS_BINDABLE) !== 0;
  var lazy = (flags2 & PROPS_IS_LAZY_INITIAL) !== 0;
  var fallback_value = (
    /** @type {V} */
    fallback2
  );
  var fallback_dirty = true;
  var fallback_signal = (
    /** @type {Derived<V> | undefined} */
    void 0
  );
  var get_fallback = () => {
    if (lazy && runes) {
      fallback_signal ?? (fallback_signal = derived(
        /** @type {() => V} */
        fallback2
      ));
      return get(fallback_signal);
    }
    if (fallback_dirty) {
      fallback_dirty = false;
      fallback_value = lazy ? untrack(
        /** @type {() => V} */
        fallback2
      ) : (
        /** @type {V} */
        fallback2
      );
    }
    return fallback_value;
  };
  let setter;
  if (bindable) {
    var is_entry_props = STATE_SYMBOL in props || LEGACY_PROPS in props;
    setter = get_descriptor(props, key2)?.set ?? (is_entry_props && key2 in props ? (v) => props[key2] = v : void 0);
  }
  var initial_value;
  var is_store_sub = false;
  if (bindable) {
    [initial_value, is_store_sub] = capture_store_binding(() => (
      /** @type {V} */
      props[key2]
    ));
  } else {
    initial_value = /** @type {V} */
    props[key2];
  }
  if (initial_value === void 0 && fallback2 !== void 0) {
    initial_value = get_fallback();
    if (setter) {
      if (runes) props_invalid_value(key2);
      setter(initial_value);
    }
  }
  var getter;
  if (runes) {
    getter = () => {
      var value = (
        /** @type {V} */
        props[key2]
      );
      if (value === void 0) return get_fallback();
      fallback_dirty = true;
      return value;
    };
  } else {
    getter = () => {
      var value = (
        /** @type {V} */
        props[key2]
      );
      if (value !== void 0) {
        fallback_value = /** @type {V} */
        void 0;
      }
      return value === void 0 ? fallback_value : value;
    };
  }
  if (runes && (flags2 & PROPS_IS_UPDATED) === 0) {
    return getter;
  }
  if (setter) {
    var legacy_parent = props.$$legacy;
    return (
      /** @type {() => V} */
      (function(value, mutation) {
        if (arguments.length > 0) {
          if (!runes || !mutation || legacy_parent || is_store_sub) {
            setter(mutation ? getter() : value);
          }
          return value;
        }
        return getter();
      })
    );
  }
  var overridden = false;
  var d = ((flags2 & PROPS_IS_IMMUTABLE) !== 0 ? derived : derived_safe_equal)(() => {
    overridden = false;
    return getter();
  });
  if (dev_fallback_default) {
    d.label = key2;
  }
  if (bindable) get(d);
  var parent_effect = (
    /** @type {Effect} */
    active_effect
  );
  return (
    /** @type {() => V} */
    (function(value, mutation) {
      if (arguments.length > 0) {
        const new_value = mutation ? get(d) : runes && bindable ? proxy(value) : value;
        set(d, new_value);
        overridden = true;
        if (fallback_value !== void 0) {
          fallback_value = new_value;
        }
        return value;
      }
      if (is_destroying_effect && overridden || (parent_effect.f & DESTROYED) !== 0) {
        return d.v;
      }
      return get(d);
    })
  );
}

// content/plugins/node_modules/svelte/src/internal/client/dom/elements/custom-element.js
var SvelteElement;
if (typeof HTMLElement === "function") {
  SvelteElement = class extends HTMLElement {
    /**
     * @param {*} $$componentCtor
     * @param {*} $$slots
     * @param {ShadowRootInit | undefined} shadow_root_init
     */
    constructor($$componentCtor, $$slots, shadow_root_init) {
      super();
      /** The Svelte component constructor */
      __publicField(this, "$$ctor");
      /** Slots */
      __publicField(this, "$$s");
      /** @type {any} The Svelte component instance */
      __publicField(this, "$$c");
      /** Whether or not the custom element is connected */
      __publicField(this, "$$cn", false);
      /** @type {Record<string, any>} Component props data */
      __publicField(this, "$$d", {});
      /** `true` if currently in the process of reflecting component props back to attributes */
      __publicField(this, "$$r", false);
      /** @type {Record<string, CustomElementPropDefinition>} Props definition (name, reflected, type etc) */
      __publicField(this, "$$p_d", {});
      /** @type {Record<string, EventListenerOrEventListenerObject[]>} Event listeners */
      __publicField(this, "$$l", {});
      /** @type {Map<EventListenerOrEventListenerObject, Function>} Event listener unsubscribe functions */
      __publicField(this, "$$l_u", /* @__PURE__ */ new Map());
      /** @type {any} The managed render effect for reflecting attributes */
      __publicField(this, "$$me");
      /** @type {ShadowRoot | null} The ShadowRoot of the custom element */
      __publicField(this, "$$shadowRoot", null);
      this.$$ctor = $$componentCtor;
      this.$$s = $$slots;
      if (shadow_root_init) {
        this.$$shadowRoot = this.attachShadow(shadow_root_init);
      }
    }
    /**
     * @param {string} type
     * @param {EventListenerOrEventListenerObject} listener
     * @param {boolean | AddEventListenerOptions} [options]
     */
    addEventListener(type, listener, options) {
      this.$$l[type] = this.$$l[type] || [];
      this.$$l[type].push(listener);
      if (this.$$c) {
        const unsub = this.$$c.$on(type, listener);
        this.$$l_u.set(listener, unsub);
      }
      super.addEventListener(type, listener, options);
    }
    /**
     * @param {string} type
     * @param {EventListenerOrEventListenerObject} listener
     * @param {boolean | AddEventListenerOptions} [options]
     */
    removeEventListener(type, listener, options) {
      super.removeEventListener(type, listener, options);
      if (this.$$c) {
        const unsub = this.$$l_u.get(listener);
        if (unsub) {
          unsub();
          this.$$l_u.delete(listener);
        }
      }
    }
    async connectedCallback() {
      this.$$cn = true;
      if (!this.$$c) {
        let create_slot = function(name) {
          return (anchor) => {
            const slot2 = create_element("slot");
            if (name !== "default") slot2.name = name;
            append(anchor, slot2);
          };
        };
        await Promise.resolve();
        if (!this.$$cn || this.$$c) {
          return;
        }
        const $$slots = {};
        const existing_slots = get_custom_elements_slots(this);
        for (const name of this.$$s) {
          if (name in existing_slots) {
            if (name === "default" && !this.$$d.children) {
              this.$$d.children = create_slot(name);
              $$slots.default = true;
            } else {
              $$slots[name] = create_slot(name);
            }
          }
        }
        for (const attribute of this.attributes) {
          const name = this.$$g_p(attribute.name);
          if (!(name in this.$$d)) {
            this.$$d[name] = get_custom_element_value(name, attribute.value, this.$$p_d, "toProp");
          }
        }
        for (const key2 in this.$$p_d) {
          if (!(key2 in this.$$d) && this[key2] !== void 0) {
            this.$$d[key2] = this[key2];
            delete this[key2];
          }
        }
        this.$$c = createClassComponent({
          component: this.$$ctor,
          target: this.$$shadowRoot || this,
          props: {
            ...this.$$d,
            $$slots,
            $$host: this
          }
        });
        this.$$me = effect_root(() => {
          render_effect(() => {
            this.$$r = true;
            for (const key2 of object_keys(this.$$c)) {
              if (!this.$$p_d[key2]?.reflect) continue;
              this.$$d[key2] = this.$$c[key2];
              const attribute_value = get_custom_element_value(
                key2,
                this.$$d[key2],
                this.$$p_d,
                "toAttribute"
              );
              if (attribute_value == null) {
                this.removeAttribute(this.$$p_d[key2].attribute || key2);
              } else {
                this.setAttribute(this.$$p_d[key2].attribute || key2, attribute_value);
              }
            }
            this.$$r = false;
          });
        });
        for (const type in this.$$l) {
          for (const listener of this.$$l[type]) {
            const unsub = this.$$c.$on(type, listener);
            this.$$l_u.set(listener, unsub);
          }
        }
        this.$$l = {};
      }
    }
    // We don't need this when working within Svelte code, but for compatibility of people using this outside of Svelte
    // and setting attributes through setAttribute etc, this is helpful
    /**
     * @param {string} attr
     * @param {string} _oldValue
     * @param {string} newValue
     */
    attributeChangedCallback(attr2, _oldValue, newValue) {
      if (this.$$r) return;
      attr2 = this.$$g_p(attr2);
      this.$$d[attr2] = get_custom_element_value(attr2, newValue, this.$$p_d, "toProp");
      this.$$c?.$set({ [attr2]: this.$$d[attr2] });
    }
    disconnectedCallback() {
      this.$$cn = false;
      Promise.resolve().then(() => {
        if (!this.$$cn && this.$$c) {
          this.$$c.$destroy();
          this.$$me();
          this.$$c = void 0;
        }
      });
    }
    /**
     * @param {string} attribute_name
     */
    $$g_p(attribute_name) {
      return object_keys(this.$$p_d).find(
        (key2) => this.$$p_d[key2].attribute === attribute_name || !this.$$p_d[key2].attribute && key2.toLowerCase() === attribute_name
      ) || attribute_name;
    }
  };
}
function get_custom_element_value(prop2, value, props_definition, transform) {
  const type = props_definition[prop2]?.type;
  value = type === "Boolean" && typeof value !== "boolean" ? value != null : value;
  if (!transform || !props_definition[prop2]) {
    return value;
  } else if (transform === "toAttribute") {
    switch (type) {
      case "Object":
      case "Array":
        return value == null ? null : JSON.stringify(value);
      case "Boolean":
        return value ? "" : null;
      case "Number":
        return value == null ? null : value;
      default:
        return value;
    }
  } else {
    switch (type) {
      case "Object":
      case "Array":
        return value && JSON.parse(value);
      case "Boolean":
        return value;
      // conversion already handled above
      case "Number":
        return value != null ? +value : value;
      default:
        return value;
    }
  }
}
function get_custom_elements_slots(element2) {
  const result = {};
  element2.childNodes.forEach((node) => {
    result[
      /** @type {Element} node */
      node.slot || "default"
    ] = true;
  });
  return result;
}

// content/plugins/marketplace/comfyui-backend/frontend/src/floating.js
function floating(node, params) {
  let { anchor, onOutsideClick, offset = 4 } = params || {};
  node.style.position = "fixed";
  node.style.margin = "0";
  document.body.appendChild(node);
  function reposition() {
    if (!anchor) return;
    const a = anchor.getBoundingClientRect();
    const n = node.getBoundingClientRect();
    let top = a.bottom + offset;
    if (top + n.height > window.innerHeight && a.top - n.height - offset >= 0) {
      top = a.top - n.height - offset;
    }
    let left = a.left;
    if (left + n.width > window.innerWidth) {
      left = Math.max(8, window.innerWidth - n.width - 8);
    }
    node.style.top = `${Math.max(8, top)}px`;
    node.style.left = `${left}px`;
  }
  reposition();
  window.addEventListener("scroll", reposition, true);
  window.addEventListener("resize", reposition);
  function handleOutsideClick(e) {
    if (node.contains(e.target)) return;
    if (anchor && anchor.contains(e.target)) return;
    onOutsideClick?.();
  }
  const armTimer = setTimeout(() => document.addEventListener("click", handleOutsideClick, true), 0);
  return {
    update(next2) {
      anchor = next2?.anchor;
      onOutsideClick = next2?.onOutsideClick;
      offset = next2?.offset ?? 4;
      reposition();
    },
    destroy() {
      clearTimeout(armTimer);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
      document.removeEventListener("click", handleOutsideClick, true);
      node.remove();
    }
  };
}

// content/plugins/marketplace/comfyui-backend/frontend/src/ImportWorkflowTab.svelte
var icon = ($$anchor, name = noop, $$arg1) => {
  let size = derived_safe_equal(() => fallback($$arg1?.(), 12));
  var svg_1 = root_1();
  var use = child(svg_1);
  reset(svg_1);
  template_effect(() => {
    set_attribute2(svg_1, "width", get(size));
    set_attribute2(svg_1, "height", get(size));
    set_attribute2(use, "href", `#i-${name()}`);
  });
  append($$anchor, svg_1);
};
var tabIcon = ($$anchor, name = noop, $$arg1) => {
  let size = derived_safe_equal(() => fallback($$arg1?.(), 13));
  var svg_2 = root_1();
  var use_1 = child(svg_2);
  reset(svg_2);
  template_effect(() => {
    set_attribute2(svg_2, "width", get(size));
    set_attribute2(svg_2, "height", get(size));
    set_attribute2(use_1, "href", `#ti-${name()}`);
  });
  append($$anchor, svg_2);
};
var root = from_svg(`<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>`);
var root_1 = from_svg(`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><use></use></svg>`);
var root_2 = from_html(`<div class="di-add-menu svelte-10v1sym"><button type="button" data-add-kind="field" class="svelte-10v1sym"><!>Field<span class="type-tag svelte-10v1sym">input</span></button> <button type="button" data-add-kind="row" class="svelte-10v1sym"><!>Row<span class="type-tag svelte-10v1sym">layout</span></button> <button type="button" data-add-kind="group" class="svelte-10v1sym"><!>Group<span class="type-tag svelte-10v1sym">layout</span></button> <button type="button" data-add-kind="section" class="svelte-10v1sym"><!>Section<span class="type-tag svelte-10v1sym">section</span></button> <button type="button" data-add-kind="header" class="svelte-10v1sym"><!>Header<span class="type-tag svelte-10v1sym">display</span></button></div>`);
var root_3 = from_html(`<div class="di-add-wrap svelte-10v1sym"><button type="button" class="di-add-field svelte-10v1sym" data-action="open-add-menu"><!> Add</button> <!></div>`);
var root_4 = from_html(`<button type="button" data-action="move-to-tab" class="svelte-10v1sym"> </button>`);
var root_5 = from_html(`<div class="tab-popover svelte-10v1sym"><div class="popover-label svelte-10v1sym">Move to</div> <!></div>`);
var root_6 = from_html(`<button type="button" class="iconbtn svelte-10v1sym" title="Move to tab" data-action="move-to-tab-menu"><!></button> <!>`, 1);
var root_7 = from_html(`<option> </option>`);
var root_8 = from_html(`<div class="di-mapping svelte-10v1sym"><span class="line mono svelte-10v1sym"><span class="arrow svelte-10v1sym">\u2192</span> </span></div>`);
var root_9 = from_html(`<div class="di-suggest-row svelte-10v1sym"><span class="di-suggest-text svelte-10v1sym">Matches workflow input <span class="mono svelte-10v1sym"> </span></span> <button type="button" class="di-suggest-map svelte-10v1sym" data-action="apply-name-match">Map</button></div>`);
var root_10 = from_html(`<div class="di-suggest svelte-10v1sym" data-name-suggestions=""><div class="di-suggest-rows svelte-10v1sym"></div> <button type="button" class="di-suggest-dismiss svelte-10v1sym" aria-label="Dismiss suggestion" data-action="dismiss-name-match"><!></button></div>`);
var root_11 = from_html(`<span class="chip chip-info svelte-10v1sym" data-name-match-chip="">name match</span>`);
var root_12 = from_html(`<span class="di-mapedit-taken svelte-10v1sym"> </span>`);
var root_13 = from_html(`<div class="di-mapedit-transform svelte-10v1sym"><select class="svelte-10v1sym"></select></div>`);
var root_14 = from_html(`<div><input type="checkbox" class="svelte-10v1sym"/> <span class="di-mapedit-node mono svelte-10v1sym"> </span> <span class="di-mapedit-input mono svelte-10v1sym"> </span> <!> <!></div>`);
var root_15 = from_html(`<div class="di-mapedit svelte-10v1sym" data-mapping-editor=""><div class="di-mapedit-label svelte-10v1sym">Mapped workflow inputs</div> <div class="di-mapedit-list svelte-10v1sym"></div></div>`);
var root_16 = from_html(`<div class="di-field-card svelte-10v1sym"><div class="di-field-top svelte-10v1sym"><span class="drag-handle svelte-10v1sym" title="Drag to move to another tab" draggable="true"><!></span> <input class="di-field-label svelte-10v1sym" type="text" aria-label="Field label"/> <select class="di-field-type svelte-10v1sym" aria-label="Field type"></select> <input class="di-field-default svelte-10v1sym" type="text" aria-label="Default value"/> <div class="di-field-actions svelte-10v1sym"><button type="button" title="Edit mapping" data-action="toggle-mapping"><!></button> <button type="button" class="iconbtn svelte-10v1sym" title="Move up" data-action="move-up"><!></button> <button type="button" class="iconbtn svelte-10v1sym" title="Move down" data-action="move-down"><!></button> <!> <button type="button" class="iconbtn svelte-10v1sym" title="Remove" data-action="remove"><!></button></div></div> <!> <!></div>`);
var root_17 = from_html(`<div class="di-header-card svelte-10v1sym"><span class="drag-handle svelte-10v1sym" title="Drag to move to another tab" draggable="true"><!></span> <span class="chip chip-warn svelte-10v1sym">HEADER</span> <input class="di-header-input svelte-10v1sym" type="text" aria-label="Header text"/> <div class="di-container-actions svelte-10v1sym"><button type="button" class="iconbtn svelte-10v1sym" title="Move up" data-action="move-up"><!></button> <button type="button" class="iconbtn svelte-10v1sym" title="Move down" data-action="move-down"><!></button> <!> <button type="button" class="iconbtn svelte-10v1sym" title="Remove" data-action="remove"><!></button></div></div>`);
var root_18 = from_html(`<!> <!>`, 1);
var root_19 = from_html(`<input class="di-container-title svelte-10v1sym" type="text" aria-label="Container title"/>`);
var root_20 = from_html(`<button type="button" class="iconbtn svelte-10v1sym" title="Toggle section" data-action="toggle-section"><span><!></span></button>`);
var root_21 = from_html(`<button type="button"> </button>`);
var root_22 = from_html(`<div class="di-col-control svelte-10v1sym"><span class="lbl svelte-10v1sym">Columns</span> <!></div>`);
var root_23 = from_html(`<button type="button" class="iconbtn svelte-10v1sym" data-action="convert"><!></button>`);
var root_24 = from_html(`<div class="di-row-cols svelte-10v1sym"></div> <!>`, 1);
var root_25 = from_html(`<div><div class="di-container-head svelte-10v1sym"><span class="drag-handle svelte-10v1sym" title="Drag to move to another tab" draggable="true"><!></span> <span> </span> <!> <!> <div class="di-container-spacer svelte-10v1sym"></div> <!> <div class="di-container-actions svelte-10v1sym"><!> <button type="button" class="iconbtn svelte-10v1sym" title="Move up" data-action="move-up"><!></button> <button type="button" class="iconbtn svelte-10v1sym" title="Move down" data-action="move-down"><!></button> <!> <button type="button" class="iconbtn svelte-10v1sym" title="Remove" data-action="remove"><!></button></div></div> <div class="di-container-body svelte-10v1sym"><!></div></div>`);
var root_26 = from_html(`<div class="wiz-sub mono svelte-10v1sym"> </div>`);
var root_27 = from_html(`<div class="wiz-sub svelte-10v1sym">checking\u2026</div>`);
var root_28 = from_html(`<div class="edit-banner svelte-10v1sym" data-import-editing="">Editing <strong class="svelte-10v1sym"> </strong> \u2014 Continue updates it in place, it won't become a new preset.</div>`);
var root_29 = from_html(`<div class="req-loading svelte-10v1sym"><span class="spinner svelte-10v1sym" aria-hidden="true"></span>Loading the preset's source workflow\u2026</div>`);
var root_30 = from_html(`<p class="message message-error svelte-10v1sym" data-import-analyze-error=""> </p>`);
var root_31 = from_html(`<p class="desc svelte-10v1sym">This preset's stored workflow is already loaded - Continue to keep it, or load a different one.</p> <div class="detected-strip svelte-10v1sym" data-import-detected=""><span class="chip chip-info svelte-10v1sym" data-import-format=""> </span> <span class="dim svelte-10v1sym">\xB7</span> <span class="mono svelte-10v1sym"> </span> <span class="dim svelte-10v1sym">\xB7</span> <span>Loaded from the existing preset</span> <button type="button" class="link-btn strip-end svelte-10v1sym">Change workflow</button></div> <!>`, 1);
var root_32 = from_html(`<p class="desc svelte-10v1sym">Paste or drop a ComfyUI Export (API) workflow JSON. In ComfyUI, enable Dev mode options in settings, then use Workflow \u2192 Export (API).</p> <div role="group" aria-label="Workflow JSON"><textarea rows="12" data-import-json-input="" class="svelte-10v1sym"></textarea> <div class="dropzone-footer svelte-10v1sym"><span class="dim svelte-10v1sym">or</span> <button type="button" class="link-btn svelte-10v1sym">choose a .json file</button> <input type="file" accept=".json,application/json" class="file-input-hidden svelte-10v1sym"/></div></div> <!>`, 1);
var root_33 = from_html(`<h3 class="svelte-10v1sym">Choose a workflow</h3> <!>`, 1);
var root_34 = from_html(`<span class="dim svelte-10v1sym">\xB7</span> <span class="dim svelte-10v1sym" data-import-object-info-used="">ranges + options from your ComfyUI</span>`, 1);
var root_35 = from_html(`<span class="dim svelte-10v1sym">\xB7</span> <span class="chip chip-info svelte-10v1sym">LoRA chain found</span>`, 1);
var root_36 = from_html(`<span class="dim svelte-10v1sym" data-import-chat-hint="">Ask the assistant to map inputs</span>`);
var root_37 = from_html(`<span class="chip chip-info svelte-10v1sym">\u2192 picker</span>`);
var root_38 = from_html(`<div class="di-row svelte-10v1sym"><span class="di-row-name mono svelte-10v1sym"> </span> <span class="di-row-value mono svelte-10v1sym"> </span> <span class="dim mono svelte-10v1sym"> </span> <!> <div class="di-row-trail svelte-10v1sym"><button type="button" data-action="lora-keep-fixed"><!></button></div></div>`);
var root_39 = from_html(`<p class="message message-error svelte-10v1sym" data-lora-sandwich-error=""> </p>`);
var root_40 = from_html(`<div class="lora-chain-card svelte-10v1sym" data-import-lora-chain=""><div class="di-group-h svelte-10v1sym">LoRA chain</div> <!> <!> <div class="lora-chain-actions svelte-10v1sym"><button type="button" class="link-btn svelte-10v1sym" data-action="convert-lora-picker"> </button></div></div>`);
var root_41 = from_html(`<span class="lock-badge svelte-10v1sym"><!>always wired</span>`);
var root_42 = from_html(`<span class="di-row-value svelte-10v1sym"> </span>`);
var root_43 = from_html(`<span class="di-row-value mono svelte-10v1sym"> </span> <span class="chip chip-info svelte-10v1sym"> </span>`, 1);
var root_44 = from_html(`<button type="button" class="iconbtn di-add-arrow svelte-10v1sym" title="Add to form" data-action="add-input"><!></button>`);
var root_45 = from_html(`<div><span class="di-row-name mono svelte-10v1sym"> </span> <!> <div class="di-row-trail svelte-10v1sym"><!></div></div>`);
var root_46 = from_html(`<div class="di-group-h svelte-10v1sym"> <span class="cls mono svelte-10v1sym"> </span></div> <!>`, 1);
var root_47 = from_html(`<input class="di-tab-rename svelte-10v1sym" type="text"/>`);
var root_48 = from_html(`<div class="tab-popover svelte-10v1sym"><button type="button" data-action="rename-tab" class="svelte-10v1sym"><!>Rename</button> <button type="button" data-action="move-tab-left" class="svelte-10v1sym"><!>Move left</button> <button type="button" data-action="move-tab-right" class="svelte-10v1sym"><!>Move right</button> <hr class="svelte-10v1sym"/> <div class="popover-label svelte-10v1sym">Icon</div> <select class="tab-popover-select svelte-10v1sym" data-action="tab-icon"></select> <div class="popover-label svelte-10v1sym">Display</div> <select class="tab-popover-select svelte-10v1sym" data-action="tab-display"></select> <hr class="svelte-10v1sym"/> <button type="button" class="danger svelte-10v1sym" data-action="delete-tab"><!>Delete tab</button></div>`);
var root_49 = from_html(`<span role="tab" tabindex="0"><!> <!> <span class="kb svelte-10v1sym" role="button" tabindex="0" title="Tab options" data-action="tab-menu"><!></span> <!></span>`);
var root_50 = from_html(`<div class="di-empty svelte-10v1sym"><!> <div class="di-empty-text svelte-10v1sym">Drop inputs here or click Add on the left</div></div>`);
var root_51 = from_html(`<option></option>`);
var root_52 = from_html(`<h3 class="svelte-10v1sym">Design the form</h3> <p class="desc svelte-10v1sym">Pick which workflow inputs become fields, then arrange them into tabs. Anything left unmapped keeps the value baked into the workflow.</p> <div class="detected-strip svelte-10v1sym" data-import-detected=""><span class="chip chip-info svelte-10v1sym" data-import-format=""> </span> <span class="dim svelte-10v1sym">\xB7</span> <span class="mono svelte-10v1sym"> </span> <!> <!> <span class="strip-end chat-hint-group svelte-10v1sym"><!> <button type="button" class="link-btn svelte-10v1sym">Change workflow</button></span></div> <div class="designer svelte-10v1sym"><div class="di-left svelte-10v1sym" data-import-form-inputs=""><div class="di-left-title svelte-10v1sym">Workflow inputs</div> <!> <div class="di-search svelte-10v1sym"><input type="text" placeholder="Search inputs\u2026" class="svelte-10v1sym"/></div> <!></div> <div class="di-right svelte-10v1sym"><div class="di-tabs svelte-10v1sym" data-import-form-tabs=""><!> <span class="di-tab-add svelte-10v1sym" role="button" tabindex="0" data-action="add-tab"><!>Add tab</span></div> <div class="di-field-list svelte-10v1sym" data-import-form-items=""><!> <!> <!></div></div></div> <div class="name-grid svelte-10v1sym"><div class="field svelte-10v1sym"><label for="import-model-family" class="svelte-10v1sym">Model family</label> <input id="import-model-family" type="text" list="import-model-family-list" placeholder="e.g. SDXL" class="svelte-10v1sym"/> <datalist id="import-model-family-list"></datalist></div> <div class="field svelte-10v1sym"><label for="import-variant" class="svelte-10v1sym">Variant</label> <input id="import-variant" type="text" placeholder="imported" class="svelte-10v1sym"/></div> <div class="field svelte-10v1sym"><label for="import-display-name" class="svelte-10v1sym">Display name</label> <input id="import-display-name" type="text" placeholder="e.g. SDXL - My workflow" class="svelte-10v1sym"/></div></div>`, 1);
var root_53 = from_html(`<div class="hist-jinja-row svelte-10v1sym"><span class="lbl svelte-10v1sym">Template</span> <input class="hist-jinja-input mono svelte-10v1sym" type="text"/> <span class="hist-jinja-out svelte-10v1sym">\u2192 <span class="v mono svelte-10v1sym"> </span></span></div>`);
var root_54 = from_html(`<div><div class="hist-reorder svelte-10v1sym"><button type="button" class="iconbtn svelte-10v1sym" title="Move up" data-action="move-up"><!></button> <button type="button" class="iconbtn svelte-10v1sym" title="Move down" data-action="move-down"><!></button></div> <input type="checkbox" data-action="toggle-emit" class="svelte-10v1sym"/> <div class="hist-field svelte-10v1sym"><span class="hist-field-label svelte-10v1sym"> </span> <span class="hist-field-name mono svelte-10v1sym"> </span></div> <input class="hist-label-input svelte-10v1sym" type="text"/> <select class="hist-value-select svelte-10v1sym"></select> <!></div>`);
var root_55 = from_html(`<span class="chip chip-info svelte-10v1sym"> </span>`);
var root_56 = from_html(`<div class="preview-cell span2 svelte-10v1sym"><div class="preview-k svelte-10v1sym"> </div> <div class="preview-chips svelte-10v1sym"></div></div>`);
var root_57 = from_html(`<div class="preview-cell svelte-10v1sym"><div class="preview-k svelte-10v1sym"> </div><div class="preview-v tabular svelte-10v1sym"> </div></div>`);
var root_58 = from_html(`<div class="preview-empty svelte-10v1sym"><!> <div class="preview-empty-text svelte-10v1sym">Nothing extra will be recorded \u2014 prompt, seed and quantity always are.</div></div>`);
var root_59 = from_html(`<h3 class="svelte-10v1sym">What shows in history</h3> <p class="desc svelte-10v1sym">Pick the form fields whose values are recorded on every generation, and how they read in the history card.</p> <div class="hist-designer svelte-10v1sym"><div class="hist-left svelte-10v1sym"><div class="pane-title svelte-10v1sym">Recorded fields <span class="n svelte-10v1sym"> </span></div> <div class="hist-table svelte-10v1sym" data-history-table=""><div class="hist-head svelte-10v1sym"><span></span><span></span><span>Field</span><span>Label in history</span><span>Value</span></div> <div class="hist-row locked svelte-10v1sym"><span></span><span></span><div class="hist-field svelte-10v1sym"><span class="hist-field-label svelte-10v1sym">Prompt</span></div><input class="hist-label-input svelte-10v1sym" type="text" value="Prompt" disabled=""/><span class="lock-badge svelte-10v1sym"><!>always recorded</span></div> <div class="hist-row locked svelte-10v1sym"><span></span><span></span><div class="hist-field svelte-10v1sym"><span class="hist-field-label svelte-10v1sym">Negative prompt</span></div><input class="hist-label-input svelte-10v1sym" type="text" value="Negative prompt" disabled=""/><span class="lock-badge svelte-10v1sym"><!>always recorded</span></div> <div class="hist-row locked svelte-10v1sym"><span></span><span></span><div class="hist-field svelte-10v1sym"><span class="hist-field-label svelte-10v1sym">Seed</span></div><input class="hist-label-input svelte-10v1sym" type="text" value="Seed" disabled=""/><span class="lock-badge svelte-10v1sym"><!>always recorded</span></div> <div class="hist-row locked svelte-10v1sym"><span></span><span></span><div class="hist-field svelte-10v1sym"><span class="hist-field-label svelte-10v1sym">Quantity</span></div><input class="hist-label-input svelte-10v1sym" type="text" value="Quantity" disabled=""/><span class="lock-badge svelte-10v1sym"><!>always recorded</span></div> <!></div></div> <div class="hist-right svelte-10v1sym"><div class="pane-title svelte-10v1sym">History card preview</div> <div class="preview-card svelte-10v1sym" data-history-preview=""><div class="preview-head svelte-10v1sym"><div class="t svelte-10v1sym"><!>Parameters</div> <span class="chip chip-mute svelte-10v1sym">#1</span></div> <div class="preview-grid svelte-10v1sym"><div class="preview-cell span2 svelte-10v1sym"><div class="preview-k svelte-10v1sym">Prompt</div><div class="preview-v wrap svelte-10v1sym">cinematic wide shot of a lighthouse at dusk</div></div> <div class="preview-cell span2 svelte-10v1sym"><div class="preview-k svelte-10v1sym">Negative prompt</div><div class="preview-v wrap svelte-10v1sym">blurry, low quality</div></div> <div class="preview-cell svelte-10v1sym"><div class="preview-k svelte-10v1sym">Seed</div><div class="preview-v tabular svelte-10v1sym">429218</div></div> <div class="preview-cell svelte-10v1sym"><div class="preview-k svelte-10v1sym">Quantity</div><div class="preview-v tabular svelte-10v1sym">4</div></div> <!></div> <!></div></div></div>`, 1);
var root_60 = from_html(`<div class="req-loading svelte-10v1sym"><span class="spinner svelte-10v1sym" aria-hidden="true"></span>Checking requirements\u2026</div>`);
var root_61 = from_html(`<p class="message message-error svelte-10v1sym"> </p>`);
var root_62 = from_html(`<div class="req-empty svelte-10v1sym">This workflow needs nothing beyond ComfyUI's own built-in nodes.</div>`);
var root_63 = from_html(`<div class="req-hint svelte-10v1sym"> </div>`);
var root_64 = from_html(`<div class="req-row svelte-10v1sym"><div></div> <div><div class="req-name mono svelte-10v1sym"> </div> <div class="req-detail svelte-10v1sym"> </div> <!></div></div>`);
var root_65 = from_html(`<div class="req-list svelte-10v1sym" data-import-requirements=""></div>`);
var root_66 = from_html(`<p class="message message-error svelte-10v1sym" data-import-create-error=""> </p>`);
var root_67 = from_html(`<h3 class="svelte-10v1sym">Requirements</h3> <p class="desc svelte-10v1sym">What this preset will need to run, detected from the workflow's nodes and models.</p> <div class="well svelte-10v1sym"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:rgb(var(--info, 91 157 255));margin-top:1px" aria-hidden="true"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="11" x2="12" y2="16.5"></line><circle cx="12" cy="7.5" r="0.75" fill="currentColor" stroke="none"></circle></svg> <span>You can still create the preset \u2014 it won't run until these are installed.</span></div> <!> <!>`, 1);
var root_68 = from_html(`<li> </li>`);
var root_69 = from_html(`<div class="message message-error svelte-10v1sym"><p class="message-title svelte-10v1sym">Lint errors</p> <ul class="svelte-10v1sym"></ul></div>`);
var root_70 = from_html(`<div class="lint-warn svelte-10v1sym"><div class="lint-warn-title svelte-10v1sym">Lint warnings</div> <div class="lint-warn-body svelte-10v1sym"> </div></div>`);
var root_71 = from_html(`<p class="message message-success svelte-10v1sym">Lint clean - no issues found.</p>`);
var root_72 = from_html(`<h3 class="svelte-10v1sym">Preset created</h3> <p class="desc svelte-10v1sym"> </p> <div class="lint-block svelte-10v1sym" data-import-lint=""><p class="lint-path svelte-10v1sym">Preset created at <span class="mono svelte-10v1sym"> </span></p> <!> <!> <!></div>`, 1);
var root_73 = from_html(`<button type="button" class="btn btn-secondary svelte-10v1sym">Import another</button> <div class="footer-spacer svelte-10v1sym"></div> <a class="btn btn-primary svelte-10v1sym" data-import-open-preset="">Open in Presets</a>`, 1);
var root_74 = from_html(`<button type="button" class="btn btn-secondary svelte-10v1sym">Back</button>`);
var root_75 = from_html(`<button type="button" class="btn btn-primary svelte-10v1sym" data-import-analyze=""> </button>`);
var root_76 = from_html(`<button type="button" class="btn btn-primary svelte-10v1sym" data-import-continue-form="">Continue</button>`);
var root_77 = from_html(`<button type="button" class="btn btn-primary svelte-10v1sym" data-import-continue-history="">Continue</button>`);
var root_78 = from_html(`<button type="button" class="btn btn-primary svelte-10v1sym" data-import-create=""> </button>`);
var root_79 = from_html(`<!> <div class="footer-spacer svelte-10v1sym"></div> <!>`, 1);
var root_80 = from_html(`<svg style="display:none" aria-hidden="true"><defs><symbol id="i-check" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></symbol><symbol id="i-lock" viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="2"></rect><path d="M7 11V7a5 5 0 0 1 10 0v4" fill="none" stroke="currentColor" stroke-width="2"></path></symbol><symbol id="i-arrow-right" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><polyline points="12 5 19 12 12 19" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></symbol><symbol id="i-arrow-left" viewBox="0 0 24 24"><line x1="19" y1="12" x2="5" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><polyline points="12 19 5 12 12 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></symbol><symbol id="i-grip" viewBox="0 0 24 24"><circle cx="9" cy="6" r="1.4" fill="currentColor"></circle><circle cx="9" cy="12" r="1.4" fill="currentColor"></circle><circle cx="9" cy="18" r="1.4" fill="currentColor"></circle><circle cx="15" cy="6" r="1.4" fill="currentColor"></circle><circle cx="15" cy="12" r="1.4" fill="currentColor"></circle><circle cx="15" cy="18" r="1.4" fill="currentColor"></circle></symbol><symbol id="i-chevron-up" viewBox="0 0 24 24"><polyline points="18 15 12 9 6 15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></symbol><symbol id="i-chevron-down" viewBox="0 0 24 24"><polyline points="6 9 12 15 18 9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></polyline></symbol><symbol id="i-x" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="6" y1="6" x2="18" y2="18" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line></symbol><symbol id="i-link" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.6" fill="currentColor"></circle><circle cx="12" cy="12" r="1.6" fill="currentColor"></circle><circle cx="19" cy="12" r="1.6" fill="currentColor"></circle></symbol><symbol id="i-plus" viewBox="0 0 24 24"><line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line></symbol><symbol id="i-pencil" viewBox="0 0 24 24"><path d="M12 20h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="i-columns" viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2"></rect><rect x="14" y="4" width="7" height="16" rx="1" fill="none" stroke="currentColor" stroke-width="2"></rect></symbol><symbol id="i-folder" viewBox="0 0 24 24"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></path></symbol><symbol id="i-layers" viewBox="0 0 24 24"><polygon points="12 2 2 7 12 12 22 7 12 2" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></polygon><polyline points="2 17 12 22 22 17" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></polyline><polyline points="2 12 12 17 22 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></polyline></symbol><symbol id="i-heading" viewBox="0 0 24 24"><path d="M6 4v16M18 4v16M6 12h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path></symbol><symbol id="i-inbox" viewBox="0 0 24 24"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></polyline><path d="M5.45 5.11L2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"></path></symbol><symbol id="i-sliders" viewBox="0 0 24 24"><line x1="4" y1="21" x2="4" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="4" y1="10" x2="4" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="12" y1="21" x2="12" y2="12" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="12" y1="8" x2="12" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="20" y1="21" x2="20" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="20" y1="12" x2="20" y2="3" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="1" y1="14" x2="7" y2="14" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="9" y1="8" x2="15" y2="8" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line><line x1="17" y1="16" x2="23" y2="16" stroke="currentColor" stroke-width="2" stroke-linecap="round"></line></symbol><symbol id="ti-generation" viewBox="0 0 24 24"><path d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-settings" viewBox="0 0 24 24"><path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-lora" viewBox="0 0 24 24"><path d="m21 7.5-2.25-1.313M21 7.5v2.25m0-2.25-2.25 1.313M3 7.5l2.25-1.313M3 7.5l2.25 1.313M3 7.5v2.25m9 3 2.25-1.313M12 12.75l-2.25-1.313M12 12.75V15m0 6.75 2.25-1.313M12 21.75V19.5m0 2.25-2.25-1.313m0-16.875L12 2.25l2.25 1.313M21 14.25v2.25l-2.25 1.313m-13.5 0L3 16.5v-2.25" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-model" viewBox="0 0 24 24"><path d="M21 7.5l-9-5.25L3 7.5m18 0l-9 5.25m9-5.25v9l-9 5.25M3 7.5l9 5.25M3 7.5v9l9 5.25m0-9v9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-image" viewBox="0 0 24 24"><path d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-video" viewBox="0 0 24 24"><path d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-film" viewBox="0 0 24 24"><path d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-sparkles" viewBox="0 0 24 24"><path d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 00-2.456 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-embedding" viewBox="0 0 24 24"><path d="M7.5 7.5h-.75A2.25 2.25 0 0 0 4.5 9.75v7.5a2.25 2.25 0 0 0 2.25 2.25h7.5a2.25 2.25 0 0 0 2.25-2.25v-7.5a2.25 2.25 0 0 0-2.25-2.25h-.75m-6 3.75 3 3m0 0 3-3m-3 3V1.5m6 9h.75a2.25 2.25 0 0 1 2.25 2.25v7.5a2.25 2.25 0 0 1-2.25 2.25h-7.5a2.25 2.25 0 0 1-2.25-2.25v-.75" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-sliders" viewBox="0 0 24 24"><path d="M10.5 6h9.75M10.5 6a1.5 1.5 0 11-3 0m3 0a1.5 1.5 0 10-3 0M3.75 6H7.5m3 12h9.75m-9.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-3.75 0H7.5m9-6h3.75m-3.75 0a1.5 1.5 0 01-3 0m3 0a1.5 1.5 0 00-3 0m-9.75 0h9.75" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-face" viewBox="0 0 24 24"><path d="M15.182 15.182a4.5 4.5 0 0 1-6.364 0M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM9.75 9.75c0 .414-.168.75-.375.75S9 10.164 9 9.75 9.168 9 9.375 9s.375.336.375.75Zm-.375 0h.008v.015h-.008V9.75Zm5.625 0c0 .414-.168.75-.375.75s-.375-.336-.375-.75.168-.75.375-.75.375.336.375.75Zm-.375 0h.008v.015h-.008V9.75Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-warning" viewBox="0 0 24 24"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol><symbol id="ti-information-circle" viewBox="0 0 24 24"><path d="M11.25 11.25l.041-.02a.75.75 0 011.063.852l-.708 2.836a.75.75 0 001.063.853l.041-.021M21 12a9 9 0 11-18 0 9 9 0 0118 0zm-9-3.75h.008v.008H12V8.25z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></symbol></defs></svg> <div class="wizard svelte-10v1sym" data-import-wizard=""><div class="wiz-rail svelte-10v1sym"><div data-wiz-step="source"><div class="wiz-num svelte-10v1sym"><!></div> <div class="wiz-text svelte-10v1sym"><div class="wiz-label svelte-10v1sym">Source</div> <!></div></div> <div data-wiz-step="form"><div class="wiz-num svelte-10v1sym"><!></div> <div class="wiz-text svelte-10v1sym"><div class="wiz-label svelte-10v1sym">Form</div> <div class="wiz-sub svelte-10v1sym"> </div></div></div> <div data-wiz-step="history"><div class="wiz-num svelte-10v1sym"><!></div> <div class="wiz-text svelte-10v1sym"><div class="wiz-label svelte-10v1sym">History</div> <!></div></div> <div data-wiz-step="requirements"><div class="wiz-num svelte-10v1sym"><!></div> <div class="wiz-text svelte-10v1sym"><div class="wiz-label svelte-10v1sym">Requirements</div> <!></div></div> <div data-wiz-step="done"><div class="wiz-num svelte-10v1sym"><!></div> <div class="wiz-text svelte-10v1sym"><div class="wiz-label svelte-10v1sym">Done</div></div></div></div> <div class="wiz-body svelte-10v1sym"><div class="wiz-content svelte-10v1sym"><!> <!></div> <div class="wiz-footer svelte-10v1sym"><!></div></div></div>`, 1);
var $$css = {
  hash: "svelte-10v1sym",
  code: ".dim.svelte-10v1sym {color:rgb(var(--fg-subtle, 122 128 144));}.mono.svelte-10v1sym {font-family:ui-monospace, SFMono-Regular, Menlo, monospace;font-variant-numeric:tabular-nums;}.link-btn.svelte-10v1sym {padding:0;border:none;background:transparent;color:rgb(var(--signal, 91 157 255));font-size:12px;cursor:pointer;}.link-btn.svelte-10v1sym:hover {text-decoration:underline;}.wizard.svelte-10v1sym {display:flex;width:100%;max-width:none;border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--surface-1, 22 24 28));min-height:480px;}.wiz-rail.svelte-10v1sym {width:190px;flex-shrink:0;border-right:1px solid rgb(var(--line, 36 38 44));padding:24px 18px;}.wiz-step.svelte-10v1sym {display:flex;align-items:flex-start;gap:10px;position:relative;padding-bottom:26px;}.wiz-step.svelte-10v1sym:last-child {padding-bottom:0;}.wiz-step.svelte-10v1sym:not(:last-child)::after {content:'';position:absolute;left:10px;top:24px;bottom:4px;width:1px;background:rgb(var(--line-strong, 43 46 53));}.wiz-num.svelte-10v1sym {width:21px;height:21px;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:10.5px;font-weight:700;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;}.wiz-step.done.svelte-10v1sym .wiz-num:where(.svelte-10v1sym) {background:rgb(var(--success, 61 214 140));color:rgb(var(--canvas, 12 13 15));}.wiz-step.current.svelte-10v1sym .wiz-num:where(.svelte-10v1sym) {border:2px solid rgb(var(--signal, 91 157 255));color:rgb(var(--signal, 91 157 255));}.wiz-step.upcoming.svelte-10v1sym .wiz-num:where(.svelte-10v1sym) {border:1px solid rgb(var(--line-strong, 43 46 53));color:rgb(var(--fg-subtle, 122 128 144));}.wiz-text.svelte-10v1sym {padding-top:1px;min-width:0;}.wiz-label.svelte-10v1sym {font-size:12.5px;font-weight:600;color:rgb(var(--fg, 232 234 237));}.wiz-step.upcoming.svelte-10v1sym .wiz-label:where(.svelte-10v1sym) {color:rgb(var(--fg-subtle, 122 128 144));font-weight:500;}.wiz-sub.svelte-10v1sym {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));margin-top:2px;}.wiz-body.svelte-10v1sym {flex:1;min-width:0;display:flex;flex-direction:column;}.wiz-content.svelte-10v1sym {flex:1;padding:24px 28px;overflow-y:auto;}.wiz-content.svelte-10v1sym h3:where(.svelte-10v1sym) {font-size:15px;font-weight:600;margin:0 0 4px;color:rgb(var(--fg, 232 234 237));}.wiz-content.svelte-10v1sym .desc:where(.svelte-10v1sym) {font-size:12px;color:rgb(var(--fg-subtle, 122 128 144));margin:0 0 16px;max-width:640px;}.edit-banner.svelte-10v1sym {padding:8px 12px;margin-bottom:16px;border-radius:6px;background:rgb(var(--signal, 91 157 255) / 0.08);border:1px solid rgb(var(--signal, 91 157 255) / 0.25);color:rgb(var(--fg-muted, 169 174 184));font-size:12px;}.edit-banner.svelte-10v1sym strong:where(.svelte-10v1sym) {color:rgb(var(--fg, 232 234 237));font-weight:600;}.wiz-footer.svelte-10v1sym {border-top:1px solid rgb(var(--line, 36 38 44));padding:12px 20px;display:flex;align-items:center;gap:10px;}.footer-spacer.svelte-10v1sym {flex:1;}.dropzone.svelte-10v1sym {display:flex;flex-direction:column;border:1px dashed rgb(var(--line-strong, 43 46 53));border-radius:6px;background:rgb(var(--surface-2, 31 33 38) / 0.4);transition:border-color 0.1s ease, background-color 0.1s ease;}.dropzone.dragover.svelte-10v1sym {border-color:rgb(var(--signal, 91 157 255));background:rgb(var(--signal, 91 157 255) / 0.06);}.dropzone.svelte-10v1sym textarea:where(.svelte-10v1sym) {width:100%;box-sizing:border-box;padding:10px 12px;color:rgb(var(--fg, 232 234 237));background:transparent;border:none;resize:vertical;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;font-size:12px;line-height:1.6;}.dropzone.svelte-10v1sym textarea:where(.svelte-10v1sym):focus {outline:none;}.dropzone-footer.svelte-10v1sym {display:flex;align-items:center;gap:6px;padding:8px 12px;border-top:1px solid rgb(var(--line, 36 38 44));font-size:12px;}.file-input-hidden.svelte-10v1sym {display:none;}.detected-strip.svelte-10v1sym {display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:8px 12px;border-radius:6px;background:rgb(var(--canvas, 12 13 15));border:1px solid rgb(var(--line, 36 38 44));margin-bottom:14px;font-size:12px;color:rgb(var(--fg-muted, 169 174 184));}.strip-end.svelte-10v1sym {margin-left:auto;}.chat-hint-group.svelte-10v1sym {display:flex;align-items:center;gap:8px;}.chip.svelte-10v1sym {font-size:9.5px;text-transform:uppercase;letter-spacing:0.05em;font-weight:600;padding:2px 6px;border-radius:3px;display:inline-flex;align-items:center;gap:4px;white-space:nowrap;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;flex-shrink:0;}.chip-info.svelte-10v1sym {background:rgb(var(--info, 91 157 255) / 0.13);color:rgb(var(--info, 91 157 255));}.chip-mute.svelte-10v1sym {background:rgb(var(--surface-3, 39 42 49));color:rgb(var(--fg-subtle, 122 128 144));}.chip-warn.svelte-10v1sym {background:rgb(var(--warning, 255 197 61) / 0.13);color:rgb(var(--warning, 255 197 61));}.chip-violet.svelte-10v1sym {background:rgb(var(--violet, 144 133 233) / 0.16);color:rgb(var(--violet, 144 133 233));}.well.svelte-10v1sym {background:rgb(var(--surface-2, 31 33 38));border-radius:6px;padding:12px 14px;display:flex;gap:10px;align-items:flex-start;font-size:12px;color:rgb(var(--fg-muted, 169 174 184));margin-bottom:14px;}.req-list.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--canvas, 12 13 15));overflow:hidden;}.req-row.svelte-10v1sym {display:flex;align-items:flex-start;gap:10px;padding:10px 12px;}.req-row.svelte-10v1sym + .req-row:where(.svelte-10v1sym) {border-top:1px solid rgb(var(--line, 36 38 44));}.req-dot.svelte-10v1sym {width:8px;height:8px;border-radius:50%;margin-top:5px;flex-shrink:0;}.req-dot.ok.svelte-10v1sym {background:rgb(var(--success, 61 214 140));}.req-dot.missing.svelte-10v1sym {background:rgb(var(--danger, 255 138 138));}.req-name.svelte-10v1sym {font-size:12.5px;font-weight:600;color:rgb(var(--fg, 232 234 237));}.req-detail.svelte-10v1sym {font-size:12px;color:rgb(var(--fg-muted, 169 174 184));}.req-hint.svelte-10v1sym {font-size:11.5px;color:rgb(var(--fg-subtle, 122 128 144));margin-top:2px;}.req-empty.svelte-10v1sym {padding:12px 14px;border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;color:rgb(var(--fg-subtle, 122 128 144));font-size:12px;}.req-loading.svelte-10v1sym {display:flex;align-items:center;gap:8px;padding:12px 0;color:rgb(var(--fg-muted, 169 174 184));font-size:12px;}.spinner.svelte-10v1sym {width:13px;height:13px;border-radius:50%;border:2px solid rgb(var(--line-strong, 43 46 53));border-top-color:rgb(var(--signal, 91 157 255));\n		animation: svelte-10v1sym-spin 0.7s linear infinite;}\n	@keyframes svelte-10v1sym-spin {\n		to {\n			transform: rotate(360deg);\n		}\n	}.lint-block.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--canvas, 12 13 15));padding:14px 16px;}.lint-path.svelte-10v1sym {font-size:12px;color:rgb(var(--fg-muted, 169 174 184));margin:0 0 12px;}.lint-warn.svelte-10v1sym {border-left:2px solid rgb(var(--warning, 255 197 61));padding-left:10px;margin-bottom:8px;}.lint-warn.svelte-10v1sym:last-child {margin-bottom:0;}.lint-warn-title.svelte-10v1sym {font-size:11.5px;font-weight:600;color:rgb(var(--warning, 255 197 61));margin-bottom:2px;}.lint-warn-body.svelte-10v1sym {font-size:12px;color:rgb(var(--fg-muted, 169 174 184));}.message.svelte-10v1sym {padding:10px 14px;border-radius:6px;font-size:12px;}.message.svelte-10v1sym ul:where(.svelte-10v1sym) {margin:4px 0 0;padding-left:18px;}.message-error.svelte-10v1sym {margin:0 0 12px;color:rgb(var(--danger, 255 138 138));background:rgb(var(--danger, 255 138 138) / 0.1);border:1px solid rgb(var(--danger, 255 138 138) / 0.25);}.message-success.svelte-10v1sym {margin:0;color:rgb(var(--success, 61 214 140));background:rgb(var(--success, 61 214 140) / 0.1);border:1px solid rgb(var(--success, 61 214 140) / 0.25);}.message-title.svelte-10v1sym {margin:0 0 4px;font-weight:600;}.btn.svelte-10v1sym {height:30px;padding:0 14px;border-radius:4px;font-size:12.5px;font-weight:600;display:inline-flex;align-items:center;gap:6px;border:1px solid transparent;cursor:pointer;white-space:nowrap;font-family:inherit;text-decoration:none;}.btn[disabled].svelte-10v1sym {opacity:0.45;cursor:not-allowed;}.btn-primary.svelte-10v1sym {background:rgb(var(--accent, 255 255 255));color:rgb(var(--accent-contrast, 22 22 22));}.btn-primary.svelte-10v1sym:hover:not([disabled]) {background:rgb(var(--accent-hover, 230 230 230));}.btn-secondary.svelte-10v1sym {background:rgb(var(--surface-1, 22 24 28));border-color:rgb(var(--line-strong, 43 46 53));color:rgb(var(--fg-muted, 169 174 184));}.btn-secondary.svelte-10v1sym:hover:not([disabled]) {color:rgb(var(--fg, 232 234 237));background:rgb(var(--surface-2, 31 33 38));}.iconbtn.svelte-10v1sym {width:22px;height:22px;border-radius:4px;display:inline-flex;align-items:center;justify-content:center;color:rgb(var(--fg-subtle, 122 128 144));background:transparent;border:1px solid transparent;cursor:pointer;flex-shrink:0;}.iconbtn.svelte-10v1sym:hover,\n	.iconbtn.active.svelte-10v1sym {color:rgb(var(--fg, 232 234 237));background:rgb(var(--surface-2, 31 33 38));}.iconbtn.svelte-10v1sym:disabled {opacity:0.4;cursor:not-allowed;}.drag-handle.svelte-10v1sym {display:inline-flex;cursor:grab;flex-shrink:0;}.drag-handle.svelte-10v1sym:active {cursor:grabbing;}.name-grid.svelte-10v1sym {display:grid;grid-template-columns:repeat(3, 1fr);gap:12px;margin-top:16px;}.field.svelte-10v1sym label:where(.svelte-10v1sym) {display:block;font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));margin-bottom:5px;}.field.svelte-10v1sym input[type='text']:where(.svelte-10v1sym) {box-sizing:border-box;width:100%;height:30px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12.5px;padding:0 9px;font-family:inherit;}.field.svelte-10v1sym input[type='text']:where(.svelte-10v1sym):focus {outline:none;border-color:rgb(var(--signal, 91 157 255));}\n\n	/* ---- Form designer: two panes ---- */.designer.svelte-10v1sym {display:flex;gap:14px;align-items:flex-start;}.di-left.svelte-10v1sym {width:40%;flex-shrink:0;border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--canvas, 12 13 15));overflow:hidden;}.di-left-title.svelte-10v1sym {padding:10px 12px 8px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:rgb(var(--fg-muted, 169 174 184));}.di-search.svelte-10v1sym {padding:0 10px 10px;}.di-search.svelte-10v1sym input:where(.svelte-10v1sym) {width:100%;box-sizing:border-box;height:28px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12px;padding:0 9px;}.di-group-h.svelte-10v1sym {padding:7px 12px;background:rgb(var(--surface-1, 22 24 28));border-top:1px solid rgb(var(--line, 36 38 44));border-bottom:1px solid rgb(var(--line, 36 38 44));font-size:11.5px;font-weight:600;color:rgb(var(--fg, 232 234 237));}.di-group-h.svelte-10v1sym .cls:where(.svelte-10v1sym) {font-size:10px;font-weight:400;color:rgb(var(--fg-subtle, 122 128 144));margin-left:4px;}.di-row.svelte-10v1sym {display:flex;align-items:center;gap:8px;padding:7px 12px;}.di-row.svelte-10v1sym + .di-row:where(.svelte-10v1sym) {border-top:1px solid rgb(var(--line, 36 38 44) / 0.5);}.di-row-name.svelte-10v1sym {font-size:11px;color:rgb(var(--fg, 232 234 237));flex-shrink:0;width:76px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.di-row-value.svelte-10v1sym {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}.di-row.mapped.svelte-10v1sym .di-row-name:where(.svelte-10v1sym),\n	.di-row.mapped.svelte-10v1sym .di-row-value:where(.svelte-10v1sym) {color:rgb(var(--fg-disabled, 92 98 112));}.di-row-trail.svelte-10v1sym {flex-shrink:0;width:22px;display:flex;align-items:center;justify-content:center;}.di-add-arrow.svelte-10v1sym {color:rgb(var(--signal, 91 157 255));}.lora-chain-card.svelte-10v1sym {border-bottom:1px solid rgb(var(--line, 36 38 44));}.lora-chain-actions.svelte-10v1sym {padding:8px 12px;border-top:1px solid rgb(var(--line, 36 38 44) / 0.5);}.lock-badge.svelte-10v1sym {display:inline-flex;align-items:center;gap:4px;font-size:9px;text-transform:uppercase;letter-spacing:0.05em;color:rgb(var(--fg-disabled, 92 98 112));flex-shrink:0;white-space:nowrap;}.di-right.svelte-10v1sym {flex:1;min-width:0;border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--surface-1, 22 24 28));overflow:hidden;display:flex;flex-direction:column;}.di-tabs.svelte-10v1sym {display:flex;align-items:center;gap:2px;padding:8px 10px 0;border-bottom:1px solid rgb(var(--line, 36 38 44));flex-wrap:wrap;}.di-tab.svelte-10v1sym {display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:4px 4px 0 0;font-size:12px;font-weight:500;color:rgb(var(--fg-muted, 169 174 184));cursor:pointer;position:relative;}.di-tab.active.svelte-10v1sym {background:rgb(var(--signal, 91 157 255) / 0.1);color:rgb(var(--signal, 91 157 255));}.di-tab.drop-target.svelte-10v1sym {box-shadow:inset 0 0 0 1px rgb(var(--signal, 91 157 255));background:rgb(var(--signal, 91 157 255) / 0.08);}.di-tab.svelte-10v1sym .kb:where(.svelte-10v1sym) {width:16px;height:16px;display:inline-flex;align-items:center;justify-content:center;color:rgb(var(--fg-subtle, 122 128 144));border-radius:3px;}.di-tab.svelte-10v1sym .kb:where(.svelte-10v1sym):hover {background:rgb(var(--surface-3, 39 42 49));color:rgb(var(--fg, 232 234 237));}.di-tab-rename.svelte-10v1sym {height:20px;width:90px;border-radius:3px;border:1px solid rgb(var(--signal, 91 157 255) / 0.4);background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12px;padding:0 6px;}.di-tab-add.svelte-10v1sym {padding:7px 10px;color:rgb(var(--fg-subtle, 122 128 144));font-size:12px;display:flex;align-items:center;gap:4px;cursor:pointer;}.di-tab-add.svelte-10v1sym:hover {color:rgb(var(--fg, 232 234 237));}.di-field-list.svelte-10v1sym {padding:12px;display:flex;flex-direction:column;gap:8px;flex:1;}.di-field-card.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--canvas, 12 13 15));padding:9px 10px;}.di-field-top.svelte-10v1sym {display:flex;align-items:center;gap:8px;flex-wrap:wrap;row-gap:6px;}.di-field-label.svelte-10v1sym {width:110px;flex-shrink:0;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12px;font-weight:500;padding:0 8px;}.di-field-type.svelte-10v1sym {width:100px;flex-shrink:0;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg-muted, 169 174 184));font-size:11px;padding:0 6px;}.di-field-default.svelte-10v1sym {flex:1 1 90px;min-width:90px;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:11.5px;padding:0 8px;}.di-field-actions.svelte-10v1sym {display:flex;align-items:center;gap:1px;flex-shrink:0;}.di-mapping.svelte-10v1sym {margin-top:6px;padding-left:24px;}.di-mapping.svelte-10v1sym .line:where(.svelte-10v1sym) {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));}.di-mapping.svelte-10v1sym .arrow:where(.svelte-10v1sym) {color:rgb(var(--fg-disabled, 92 98 112));margin-right:3px;}.di-suggest.svelte-10v1sym {display:flex;align-items:flex-start;gap:8px;margin-top:6px;padding:6px 8px 6px 24px;}.di-suggest-rows.svelte-10v1sym {display:flex;flex-direction:column;gap:4px;flex:1;min-width:0;}.di-suggest-row.svelte-10v1sym {display:flex;align-items:center;gap:8px;flex-wrap:wrap;}.di-suggest-text.svelte-10v1sym {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));}.di-suggest-map.svelte-10v1sym {flex-shrink:0;height:20px;padding:0 8px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:10.5px;font-weight:600;cursor:pointer;}.di-suggest-map.svelte-10v1sym:hover {border-color:rgb(var(--line-hover, 58 62 70));background:rgb(var(--surface-3, 39 42 49));}.di-suggest-dismiss.svelte-10v1sym {flex-shrink:0;width:18px;height:18px;display:inline-flex;align-items:center;justify-content:center;border-radius:4px;color:rgb(var(--fg-disabled, 92 98 112));background:none;border:none;cursor:pointer;}.di-suggest-dismiss.svelte-10v1sym:hover {color:rgb(var(--fg-muted, 169 174 184));background:rgb(var(--surface-3, 39 42 49));}.di-add-wrap.svelte-10v1sym {position:relative;}.di-add-field.svelte-10v1sym {display:flex;align-items:center;justify-content:center;gap:6px;height:34px;width:100%;border:1px dashed rgb(var(--line-strong, 43 46 53));border-radius:6px;font-size:11.5px;color:rgb(var(--fg-muted, 169 174 184));background:none;cursor:pointer;}.di-add-field.svelte-10v1sym:hover {color:rgb(var(--fg, 232 234 237));}.di-add-menu.svelte-10v1sym {\n		/* Positioned by the `floating` action (position: fixed, inline top/left) -\n		   these are just its pre-mount fallback. */position:absolute;top:calc(100% + 4px);left:0;width:168px;border:1px solid rgb(var(--line-strong, 43 46 53));border-radius:8px;padding:4px;background:rgb(var(--surface-1, 22 24 28));box-shadow:var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));z-index:1000;}.di-add-menu.svelte-10v1sym button:where(.svelte-10v1sym) {display:flex;width:100%;align-items:center;gap:8px;border-radius:5px;padding:6px 8px;font-size:12px;color:rgb(var(--fg-muted, 169 174 184));background:none;border:none;text-align:left;cursor:pointer;}.di-add-menu.svelte-10v1sym button:where(.svelte-10v1sym):hover {color:rgb(var(--fg, 232 234 237));background:rgb(var(--surface-3, 39 42 49));}.di-add-menu.svelte-10v1sym .type-tag:where(.svelte-10v1sym) {margin-left:auto;font-size:8.5px;color:rgb(var(--fg-disabled, 92 98 112));font-family:ui-monospace, SFMono-Regular, Menlo, monospace;}.di-container.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--surface-2, 31 33 38) / 0.4);}.di-container-head.svelte-10v1sym {display:flex;align-items:center;gap:8px;padding:8px 10px;flex-wrap:wrap;}.di-container-title.svelte-10v1sym {flex-shrink:0;width:130px;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12px;font-weight:600;padding:0 8px;}.di-container-spacer.svelte-10v1sym {flex:1;}.di-container-actions.svelte-10v1sym {display:flex;align-items:center;gap:1px;flex-shrink:0;}.di-container-body.svelte-10v1sym {padding:0 10px 10px;display:flex;flex-direction:column;gap:8px;}.di-container.collapsed.svelte-10v1sym .di-container-body:where(.svelte-10v1sym) {display:none;}.di-chevron.svelte-10v1sym {display:inline-flex;transition:transform 0.12s;}.di-chevron.collapsed.svelte-10v1sym {transform:rotate(-90deg);}.di-row-cols.svelte-10v1sym {display:grid;gap:8px;min-width:0;}.di-row-cols.svelte-10v1sym > * {min-width:0;}.di-row-cols.svelte-10v1sym .di-field-card {overflow:hidden;}.di-row-cols.svelte-10v1sym .di-field-top {flex-wrap:wrap;}.di-col-control.svelte-10v1sym {display:flex;align-items:center;gap:5px;}.di-col-control.svelte-10v1sym .lbl:where(.svelte-10v1sym) {font-size:10px;color:rgb(var(--fg-subtle, 122 128 144));margin-right:2px;}.di-col-btn.svelte-10v1sym {width:22px;height:22px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg-muted, 169 174 184));font-size:11px;font-weight:600;cursor:pointer;}.di-col-btn.active.svelte-10v1sym {background:rgb(var(--signal, 91 157 255) / 0.15);color:rgb(var(--signal, 91 157 255));border-color:rgb(var(--signal, 91 157 255) / 0.4);}.di-header-card.svelte-10v1sym {display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--surface-2, 31 33 38) / 0.4);}.di-header-input.svelte-10v1sym {flex:1;min-width:0;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:12.5px;font-weight:600;padding:0 8px;}.di-empty.svelte-10v1sym {flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:40px 20px;text-align:center;}.di-empty.svelte-10v1sym .icon {color:rgb(var(--fg-disabled, 92 98 112));}.di-empty-text.svelte-10v1sym {font-size:12px;color:rgb(var(--fg-subtle, 122 128 144));}.di-mapedit.svelte-10v1sym {margin-top:8px;padding-top:10px;border-top:1px dashed rgb(var(--line, 36 38 44));}.di-mapedit-label.svelte-10v1sym {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));margin-bottom:6px;}.di-mapedit-list.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:4px;overflow:hidden;}.di-mapedit-row.svelte-10v1sym {display:flex;align-items:center;gap:8px;padding:6px 9px;font-size:11px;flex-wrap:wrap;}.di-mapedit-node.svelte-10v1sym,\n	.di-mapedit-input.svelte-10v1sym {white-space:nowrap;}.di-mapedit-row.svelte-10v1sym + .di-mapedit-row:where(.svelte-10v1sym) {border-top:1px solid rgb(var(--line, 36 38 44));}.di-mapedit-row.selected.svelte-10v1sym {background:rgb(var(--signal, 91 157 255) / 0.08);}.di-mapedit-row.svelte-10v1sym input[type='checkbox']:where(.svelte-10v1sym) {accent-color:rgb(var(--signal, 91 157 255));}.di-mapedit-node.svelte-10v1sym {color:rgb(var(--fg-subtle, 122 128 144));}.di-mapedit-input.svelte-10v1sym {color:rgb(var(--fg, 232 234 237));}.di-mapedit-taken.svelte-10v1sym {margin-left:auto;font-size:10px;color:rgb(var(--fg-disabled, 92 98 112));}.di-mapedit-transform.svelte-10v1sym {margin-left:auto;flex:1 1 100%;}.di-mapedit-transform.svelte-10v1sym select:where(.svelte-10v1sym) {box-sizing:border-box;width:100%;height:24px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:11px;padding:0 6px;}.tab-popover.svelte-10v1sym {\n		/* Positioned by the `floating` action (position: fixed, inline top/left) -\n		   these are just its pre-mount fallback. */position:absolute;top:calc(100% + 4px);left:0;width:168px;border:1px solid rgb(var(--line-strong, 43 46 53));border-radius:8px;padding:4px;background:rgb(var(--surface-1, 22 24 28));box-shadow:var(--shadow-floating, 0 4px 16px rgb(0 0 0 / 0.5));z-index:1000;}.tab-popover.svelte-10v1sym button:where(.svelte-10v1sym) {display:flex;width:100%;align-items:center;gap:8px;border-radius:5px;padding:6px 8px;font-size:12px;color:rgb(var(--fg-muted, 169 174 184));background:none;border:none;text-align:left;cursor:pointer;}.tab-popover.svelte-10v1sym button:where(.svelte-10v1sym):hover:not(:disabled) {color:rgb(var(--fg, 232 234 237));background:rgb(var(--surface-3, 39 42 49));}.tab-popover.svelte-10v1sym .popover-label:where(.svelte-10v1sym) {font-size:10.5px;color:rgb(var(--fg-subtle, 122 128 144));padding:4px 8px 2px;}.tab-popover.svelte-10v1sym button:where(.svelte-10v1sym):disabled {opacity:0.4;cursor:not-allowed;}.tab-popover.svelte-10v1sym hr:where(.svelte-10v1sym) {border:none;border-top:1px solid rgb(var(--line, 36 38 44));margin:4px 2px;}.tab-popover.svelte-10v1sym button.danger:where(.svelte-10v1sym) {color:rgb(var(--danger, 255 138 138));}.tab-popover-select.svelte-10v1sym {box-sizing:border-box;width:100%;height:24px;margin:0 0 4px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:11px;padding:0 6px;}.tab-popover-select.svelte-10v1sym:disabled {opacity:0.5;}\n\n	/* ---- History step ---- */.hist-designer.svelte-10v1sym {display:flex;gap:14px;align-items:flex-start;}.hist-left.svelte-10v1sym {width:58%;flex-shrink:0;}.hist-right.svelte-10v1sym {flex:1;min-width:0;}.pane-title.svelte-10v1sym {padding:0 0 8px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:rgb(var(--fg-muted, 169 174 184));display:flex;align-items:center;gap:8px;}.pane-title.svelte-10v1sym .n:where(.svelte-10v1sym) {font-weight:400;color:rgb(var(--fg-disabled, 92 98 112));text-transform:none;letter-spacing:0;}.hist-table.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--canvas, 12 13 15));overflow:hidden;}.hist-head.svelte-10v1sym {display:grid;grid-template-columns:40px 18px 1.3fr 1.05fr 1.05fr;align-items:center;gap:10px;padding:7px 12px;background:rgb(var(--surface-1, 22 24 28));border-bottom:1px solid rgb(var(--line, 36 38 44));font-size:9.5px;text-transform:uppercase;letter-spacing:0.06em;color:rgb(var(--fg-subtle, 122 128 144));}.hist-row.svelte-10v1sym {display:grid;grid-template-columns:40px 18px 1.3fr 1.05fr 1.05fr;align-items:center;gap:10px;padding:8px 12px;}.hist-row.svelte-10v1sym + .hist-row:where(.svelte-10v1sym) {border-top:1px solid rgb(var(--line, 36 38 44) / 0.6);}.hist-row.locked.svelte-10v1sym {background:rgb(var(--surface-1, 22 24 28) / 0.5);}.hist-reorder.svelte-10v1sym {display:flex;gap:2px;}.hist-row.svelte-10v1sym input[type='checkbox']:where(.svelte-10v1sym) {accent-color:rgb(var(--signal, 91 157 255));width:14px;height:14px;}.hist-field.svelte-10v1sym {min-width:0;display:flex;flex-direction:column;gap:1px;}.hist-field-label.svelte-10v1sym {font-size:12px;font-weight:500;color:rgb(var(--fg, 232 234 237));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}.hist-row.off.svelte-10v1sym .hist-field-label:where(.svelte-10v1sym) {color:rgb(var(--fg-subtle, 122 128 144));}.hist-field-name.svelte-10v1sym {font-size:9.5px;color:rgb(var(--fg-subtle, 122 128 144));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}.hist-label-input.svelte-10v1sym {width:100%;box-sizing:border-box;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:11.5px;padding:0 8px;}.hist-label-input.svelte-10v1sym:disabled {background:transparent;border-color:transparent;color:rgb(var(--fg-disabled, 92 98 112));padding-left:0;}.hist-value-select.svelte-10v1sym {width:100%;box-sizing:border-box;height:26px;border-radius:4px;border:1px solid rgb(var(--line-strong, 43 46 53));background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg-muted, 169 174 184));font-size:11px;padding:0 6px;}.hist-value-select.svelte-10v1sym:disabled {opacity:0.5;}.hist-jinja-row.svelte-10v1sym {grid-column:1 / -1;margin:2px 0 0 58px;display:flex;align-items:center;gap:8px;}.hist-jinja-row.svelte-10v1sym .lbl:where(.svelte-10v1sym) {font-size:10px;color:rgb(var(--fg-subtle, 122 128 144));flex-shrink:0;}.hist-jinja-input.svelte-10v1sym {flex:1;box-sizing:border-box;height:26px;border-radius:4px;border:1px solid rgb(var(--signal, 91 157 255) / 0.4);background:rgb(var(--surface-2, 31 33 38));color:rgb(var(--fg, 232 234 237));font-size:11px;padding:0 8px;}.hist-jinja-out.svelte-10v1sym {font-size:10px;color:rgb(var(--fg-subtle, 122 128 144));flex-shrink:0;}.hist-jinja-out.svelte-10v1sym .v:where(.svelte-10v1sym) {color:rgb(var(--success, 61 214 140));}.preview-card.svelte-10v1sym {border:1px solid rgb(var(--line, 36 38 44));border-radius:6px;background:rgb(var(--surface-2, 31 33 38));overflow:hidden;box-shadow:var(--shadow-raised, inset 0 1px 0 rgb(255 255 255 / 0.04));}.preview-head.svelte-10v1sym {display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid rgb(var(--line, 36 38 44));}.preview-head.svelte-10v1sym .t:where(.svelte-10v1sym) {display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:rgb(var(--fg, 232 234 237));}.preview-head.svelte-10v1sym .t:where(.svelte-10v1sym) .icon {color:rgb(var(--success, 61 214 140));}.preview-grid.svelte-10v1sym {display:grid;grid-template-columns:1fr 1fr;gap:1px;background:rgb(var(--line, 36 38 44));}.preview-cell.svelte-10v1sym {background:rgb(var(--surface-2, 31 33 38));padding:10px;min-width:0;}.preview-cell.span2.svelte-10v1sym {grid-column:1 / -1;}.preview-k.svelte-10v1sym {font-size:9.5px;text-transform:uppercase;letter-spacing:0.06em;color:rgb(var(--fg-disabled, 92 98 112));margin-bottom:4px;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;}.preview-v.svelte-10v1sym {font-size:11.5px;font-weight:600;color:rgb(var(--fg, 232 234 237));white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:ui-monospace, SFMono-Regular, Menlo, monospace;}.preview-v.wrap.svelte-10v1sym {white-space:normal;overflow:visible;text-overflow:clip;}.preview-chips.svelte-10v1sym {display:flex;flex-wrap:wrap;gap:4px;margin-top:2px;}.preview-empty.svelte-10v1sym {padding:20px 16px;text-align:center;}.preview-empty.svelte-10v1sym .icon {color:rgb(var(--fg-disabled, 92 98 112));margin:0 auto 8px;}.preview-empty-text.svelte-10v1sym {font-size:11.5px;color:rgb(var(--fg-subtle, 122 128 144));max-width:260px;margin:0 auto;}.icon {stroke:currentColor;fill:none;flex:none;display:block;}"
};
function ImportWorkflowTab($$anchor, $$props) {
  if (new.target) return createClassComponent({ component: ImportWorkflowTab, ...$$anchor });
  push($$props, true);
  append_styles($$anchor, $$css);
  const stepDot = ($$anchor2, index2 = noop) => {
    var fragment = comment();
    var node = first_child(fragment);
    {
      var consequent = ($$anchor3) => {
        var svg = root();
        append($$anchor3, svg);
      };
      var d_1 = user_derived(() => stepState(index2()) === "done");
      var alternate = ($$anchor3) => {
        var text_1 = text();
        template_effect(() => set_text(text_1, index2()));
        append($$anchor3, text_1);
      };
      if_block(node, ($$render) => {
        if (get(d_1)) $$render(consequent);
        else $$render(alternate, -1);
      });
    }
    append($$anchor2, fragment);
  };
  const addMenu = ($$anchor2, items = noop, $$arg1) => {
    let isRoot = derived_safe_equal(() => fallback($$arg1?.(), false));
    var div = root_3();
    var button = child(div);
    var node_1 = child(button);
    icon(node_1, () => "plus");
    next();
    reset(button);
    var node_2 = sibling(button, 2);
    {
      var consequent_1 = ($$anchor3) => {
        var div_1 = root_2();
        var button_1 = child(div_1);
        var node_3 = child(button_1);
        icon(node_3, () => "arrow-right");
        next(2);
        reset(button_1);
        var button_2 = sibling(button_1, 2);
        var node_4 = child(button_2);
        icon(node_4, () => "columns");
        next(2);
        reset(button_2);
        var button_3 = sibling(button_2, 2);
        var node_5 = child(button_3);
        icon(node_5, () => "folder");
        next(2);
        reset(button_3);
        var button_4 = sibling(button_3, 2);
        var node_6 = child(button_4);
        icon(node_6, () => "layers");
        next(2);
        reset(button_4);
        var button_5 = sibling(button_4, 2);
        var node_7 = child(button_5);
        icon(node_7, () => "heading");
        next(2);
        reset(button_5);
        reset(div_1);
        action(div_1, ($$node, $$action_arg) => floating?.($$node, $$action_arg), () => ({
          anchor: get(addMenuAnchorEl),
          onOutsideClick: () => set(addMenuOpenFor, null)
        }));
        delegated("click", button_1, () => addItemToContainer(items(), "field"));
        delegated("click", button_2, () => addItemToContainer(items(), "row"));
        delegated("click", button_3, () => addItemToContainer(items(), "group"));
        delegated("click", button_4, () => addItemToContainer(items(), "section"));
        delegated("click", button_5, () => addItemToContainer(items(), "header"));
        append($$anchor3, div_1);
      };
      if_block(node_2, ($$render) => {
        if (get(addMenuOpenFor) === items()) $$render(consequent_1);
      });
    }
    reset(div);
    template_effect(() => set_attribute2(div, "data-add-root", get(isRoot) ? "true" : void 0));
    delegated("click", button, (e) => toggleAddMenu(items(), e.currentTarget));
    append($$anchor2, div);
  };
  const moveToMenu = ($$anchor2, item = noop, parentItems = noop, index2 = noop) => {
    var fragment_2 = comment();
    var node_8 = first_child(fragment_2);
    {
      var consequent_3 = ($$anchor3) => {
        var fragment_3 = root_6();
        var button_6 = first_child(fragment_3);
        var node_9 = child(button_6);
        icon(node_9, () => "more");
        reset(button_6);
        var node_10 = sibling(button_6, 2);
        {
          var consequent_2 = ($$anchor4) => {
            var div_2 = root_5();
            var node_11 = sibling(child(div_2), 2);
            each(node_11, 17, () => get(form).tabs.filter((t) => t.id !== get(activeTabId)), (t) => t.id, ($$anchor5, t) => {
              var button_7 = root_4();
              var text_2 = child(button_7, true);
              reset(button_7);
              template_effect(() => {
                set_attribute2(button_7, "data-target-tab", get(t).id);
                set_text(text_2, get(t).label);
              });
              delegated("click", button_7, () => {
                moveItemToTab(parentItems(), index2(), get(t).id);
                set(moveToPopoverId, null);
              });
              append($$anchor5, button_7);
            });
            reset(div_2);
            action(div_2, ($$node, $$action_arg) => floating?.($$node, $$action_arg), () => ({
              anchor: get(moveToPopoverAnchorEl),
              onOutsideClick: () => set(moveToPopoverId, null)
            }));
            delegated("click", div_2, (e) => e.stopPropagation());
            delegated("keydown", div_2, (e) => e.stopPropagation());
            append($$anchor4, div_2);
          };
          if_block(node_10, ($$render) => {
            if (get(moveToPopoverId) === item()._id) $$render(consequent_2);
          });
        }
        delegated("click", button_6, (e) => toggleMoveToPopover(item()._id, e.currentTarget));
        append($$anchor3, fragment_3);
      };
      if_block(node_8, ($$render) => {
        if (get(form).tabs.length > 1) $$render(consequent_3);
      });
    }
    append($$anchor2, fragment_2);
  };
  const fieldCard = ($$anchor2, item = noop, parentItems = noop, index2 = noop) => {
    var div_3 = root_16();
    var div_4 = child(div_3);
    var span = child(div_4);
    var node_12 = child(span);
    icon(node_12, () => "grip");
    reset(span);
    var input = sibling(span, 2);
    remove_input_defaults(input);
    var select = sibling(input, 2);
    each(select, 21, () => fieldTypeOptionsFor(item().field_type), index, ($$anchor3, opt) => {
      var option = root_7();
      var text_3 = child(option, true);
      reset(option);
      var option_value = {};
      template_effect(() => {
        set_text(text_3, get(opt));
        if (option_value !== (option_value = get(opt))) {
          option.value = (option.__value = get(opt)) ?? "";
        }
      });
      append($$anchor3, option);
    });
    reset(select);
    var select_value;
    init_select(select);
    var input_1 = sibling(select, 2);
    remove_input_defaults(input_1);
    var div_5 = sibling(input_1, 2);
    var button_8 = child(div_5);
    let classes;
    var node_13 = child(button_8);
    icon(node_13, () => "link");
    reset(button_8);
    var button_9 = sibling(button_8, 2);
    var node_14 = child(button_9);
    icon(node_14, () => "chevron-up");
    reset(button_9);
    var button_10 = sibling(button_9, 2);
    var node_15 = child(button_10);
    icon(node_15, () => "chevron-down");
    reset(button_10);
    var node_16 = sibling(button_10, 2);
    moveToMenu(node_16, item, parentItems, index2);
    var button_11 = sibling(node_16, 2);
    var node_17 = child(button_11);
    icon(node_17, () => "x");
    reset(button_11);
    reset(div_5);
    reset(div_4);
    var node_18 = sibling(div_4, 2);
    {
      var consequent_4 = ($$anchor3) => {
        var div_6 = root_8();
        var span_1 = child(div_6);
        var text_4 = sibling(child(span_1), 1, true);
        reset(span_1);
        reset(div_6);
        template_effect(($0) => set_text(text_4, $0), [
          () => item().mappings.map((m) => `${m.node_id}.inputs.${m.input_name}`).join(" \xB7 ")
        ]);
        append($$anchor3, div_6);
      };
      var consequent_6 = ($$anchor3) => {
        const suggestions = user_derived(() => nameMatchSuggestionsFor(item()));
        var fragment_4 = comment();
        var node_19 = first_child(fragment_4);
        {
          var consequent_5 = ($$anchor4) => {
            var div_7 = root_10();
            var div_8 = child(div_7);
            each(div_8, 21, () => get(suggestions), (c) => candidateKey(c), ($$anchor5, c) => {
              var div_9 = root_9();
              var span_2 = child(div_9);
              var span_3 = sibling(child(span_2));
              var text_5 = child(span_3);
              reset(span_3);
              reset(span_2);
              var button_12 = sibling(span_2, 2);
              reset(div_9);
              template_effect(() => set_text(text_5, `${get(c).node_id ?? ""}.inputs.${get(c).input_name ?? ""}`));
              delegated("click", button_12, () => toggleMapping(item(), get(c), true));
              append($$anchor5, div_9);
            });
            reset(div_8);
            var button_13 = sibling(div_8, 2);
            var node_20 = child(button_13);
            icon(node_20, () => "x", () => 10);
            reset(button_13);
            reset(div_7);
            delegated("click", button_13, () => dismissSuggestion(item()._id));
            append($$anchor4, div_7);
          };
          if_block(node_19, ($$render) => {
            if (get(suggestions).length > 0) $$render(consequent_5);
          });
        }
        append($$anchor3, fragment_4);
      };
      if_block(node_18, ($$render) => {
        if (item().mappings.length > 0) $$render(consequent_4);
        else if (!get(dismissedSuggestionIds)[item()._id]) $$render(consequent_6, 1);
      });
    }
    var node_21 = sibling(node_18, 2);
    {
      var consequent_10 = ($$anchor3) => {
        const sortedMapCandidates = user_derived(() => [...get(mappableCandidates)].sort((a, b) => Number(candidateNameMatchesField(b, item())) - Number(candidateNameMatchesField(a, item()))));
        var div_10 = root_15();
        var div_11 = sibling(child(div_10), 2);
        each(div_11, 21, () => get(sortedMapCandidates), (c) => candidateKey(c), ($$anchor4, c) => {
          const checked = user_derived(() => item().mappings.some((m) => m.node_id === get(c).node_id && m.input_name === get(c).input_name));
          const mappedElsewhere = user_derived(() => get(mappedFieldByKey).get(candidateKey(get(c))) && get(mappedFieldByKey).get(candidateKey(get(c))) !== item());
          const isNameMatch = user_derived(() => candidateNameMatchesField(get(c), item()));
          var div_12 = root_14();
          let classes_1;
          var input_2 = child(div_12);
          remove_input_defaults(input_2);
          var span_4 = sibling(input_2, 2);
          var text_6 = child(span_4);
          reset(span_4);
          var span_5 = sibling(span_4, 2);
          var text_7 = child(span_5, true);
          reset(span_5);
          var node_22 = sibling(span_5, 2);
          {
            var consequent_7 = ($$anchor5) => {
              var span_6 = root_11();
              append($$anchor5, span_6);
            };
            if_block(node_22, ($$render) => {
              if (get(isNameMatch)) $$render(consequent_7);
            });
          }
          var node_23 = sibling(node_22, 2);
          {
            var consequent_8 = ($$anchor5) => {
              var span_7 = root_12();
              var text_8 = child(span_7);
              reset(span_7);
              template_effect(($0) => set_text(text_8, `mapped to ${$0 ?? ""}`), [
                () => get(mappedFieldByKey).get(candidateKey(get(c))).label
              ]);
              append($$anchor5, span_7);
            };
            var consequent_9 = ($$anchor5) => {
              var div_13 = root_13();
              var select_1 = child(div_13);
              each(select_1, 21, () => TRANSFORM_OPTIONS, index, ($$anchor6, t) => {
                var option_1 = root_7();
                var text_9 = child(option_1, true);
                reset(option_1);
                var option_1_value = {};
                template_effect(() => {
                  set_text(text_9, get(t).label);
                  if (option_1_value !== (option_1_value = get(t).value)) {
                    option_1.value = (option_1.__value = get(t).value) ?? "";
                  }
                });
                append($$anchor6, option_1);
              });
              reset(select_1);
              var select_1_value;
              init_select(select_1);
              reset(div_13);
              template_effect(
                ($0) => {
                  if (select_1_value !== (select_1_value = $0)) {
                    select_1.value = (select_1.__value = $0) ?? "", select_option(select_1, $0);
                  }
                },
                [
                  () => item().mappings.find((m) => m.node_id === get(c).node_id && m.input_name === get(c).input_name)?.transform || "none"
                ]
              );
              delegated("change", select_1, (e) => setMappingTransform(item(), get(c), e.currentTarget.value));
              append($$anchor5, div_13);
            };
            if_block(node_23, ($$render) => {
              if (get(mappedElsewhere)) $$render(consequent_8);
              else if (get(checked)) $$render(consequent_9, 1);
            });
          }
          reset(div_12);
          template_effect(
            ($0) => {
              classes_1 = set_class(div_12, 1, "di-mapedit-row svelte-10v1sym", null, classes_1, { selected: get(checked) });
              set_attribute2(div_12, "data-mapedit-key", $0);
              set_checked(input_2, get(checked));
              input_2.disabled = get(mappedElsewhere);
              set_text(text_6, `${get(c).node_id ?? ""} \xB7 ${get(c).class_type ?? ""}`);
              set_text(text_7, get(c).input_name);
            },
            [() => candidateKey(get(c))]
          );
          delegated("change", input_2, (e) => toggleMapping(item(), get(c), e.currentTarget.checked));
          append($$anchor4, div_12);
        });
        reset(div_11);
        reset(div_10);
        append($$anchor3, div_10);
      };
      if_block(node_21, ($$render) => {
        if (get(expandedFieldId) === item()._id) $$render(consequent_10);
      });
    }
    reset(div_3);
    template_effect(
      ($0) => {
        set_attribute2(div_3, "data-field-name", item().field_name);
        if (select_value !== (select_value = item().field_type)) {
          select.value = (select.__value = item().field_type) ?? "", select_option(select, item().field_type);
        }
        set_value(input_1, $0);
        classes = set_class(button_8, 1, "iconbtn svelte-10v1sym", null, classes, { active: get(expandedFieldId) === item()._id });
        set_attribute2(button_8, "aria-expanded", get(expandedFieldId) === item()._id);
      },
      [() => displayDefault(item())]
    );
    event("dragstart", span, (e) => handleItemDragStart(e, item()._id));
    event("dragend", span, handleItemDragEnd);
    bind_value(input, () => item().label, ($$value) => item().label = $$value);
    delegated("change", select, (e) => item().field_type = e.currentTarget.value);
    delegated("input", input_1, (e) => setDefaultFromText(item(), e.currentTarget.value));
    delegated("click", button_8, () => set(expandedFieldId, get(expandedFieldId) === item()._id ? null : item()._id, true));
    delegated("click", button_9, () => moveItemAt(parentItems(), index2(), -1));
    delegated("click", button_10, () => moveItemAt(parentItems(), index2(), 1));
    delegated("click", button_11, () => removeItemAt(parentItems(), index2()));
    append($$anchor2, div_3);
  };
  const headerCard = ($$anchor2, item = noop, parentItems = noop, index2 = noop) => {
    var div_14 = root_17();
    var span_8 = child(div_14);
    var node_24 = child(span_8);
    icon(node_24, () => "grip");
    reset(span_8);
    var input_3 = sibling(span_8, 4);
    remove_input_defaults(input_3);
    var div_15 = sibling(input_3, 2);
    var button_14 = child(div_15);
    var node_25 = child(button_14);
    icon(node_25, () => "chevron-up");
    reset(button_14);
    var button_15 = sibling(button_14, 2);
    var node_26 = child(button_15);
    icon(node_26, () => "chevron-down");
    reset(button_15);
    var node_27 = sibling(button_15, 2);
    moveToMenu(node_27, item, parentItems, index2);
    var button_16 = sibling(node_27, 2);
    var node_28 = child(button_16);
    icon(node_28, () => "x");
    reset(button_16);
    reset(div_15);
    reset(div_14);
    event("dragstart", span_8, (e) => handleItemDragStart(e, item()._id));
    event("dragend", span_8, handleItemDragEnd);
    bind_value(input_3, () => item().text, ($$value) => item().text = $$value);
    delegated("click", button_14, () => moveItemAt(parentItems(), index2(), -1));
    delegated("click", button_15, () => moveItemAt(parentItems(), index2(), 1));
    delegated("click", button_16, () => removeItemAt(parentItems(), index2()));
    append($$anchor2, div_14);
  };
  const anyItem = ($$anchor2, item = noop, parentItems = noop, index2 = noop) => {
    var fragment_5 = comment();
    var node_29 = first_child(fragment_5);
    {
      var consequent_11 = ($$anchor3) => {
        fieldCard($$anchor3, item, parentItems, index2);
      };
      var consequent_12 = ($$anchor3) => {
        headerCard($$anchor3, item, parentItems, index2);
      };
      var consequent_13 = ($$anchor3) => {
        containerCard($$anchor3, item, parentItems, index2, () => "ROW", () => "chip-violet");
      };
      var consequent_14 = ($$anchor3) => {
        containerCard($$anchor3, item, parentItems, index2, () => "GROUP", () => "chip-mute");
      };
      var consequent_15 = ($$anchor3) => {
        containerCard($$anchor3, item, parentItems, index2, () => "SECTION", () => "chip-info");
      };
      if_block(node_29, ($$render) => {
        if (item().kind === "field") $$render(consequent_11);
        else if (item().kind === "header") $$render(consequent_12, 1);
        else if (item().kind === "row") $$render(consequent_13, 2);
        else if (item().kind === "group") $$render(consequent_14, 3);
        else if (item().kind === "section") $$render(consequent_15, 4);
      });
    }
    append($$anchor2, fragment_5);
  };
  const itemsList = ($$anchor2, items = noop) => {
    var fragment_11 = root_18();
    var node_30 = first_child(fragment_11);
    each(node_30, 19, items, (it) => it._id, ($$anchor3, it, index2) => {
      anyItem($$anchor3, () => get(it), items, () => get(index2));
    });
    var node_31 = sibling(node_30, 2);
    addMenu(node_31, items);
    append($$anchor2, fragment_11);
  };
  const containerCard = ($$anchor2, item = noop, parentItems = noop, index2 = noop, chipLabel = noop, chipClass = noop) => {
    var div_16 = root_25();
    let classes_2;
    var div_17 = child(div_16);
    var span_9 = child(div_17);
    var node_32 = child(span_9);
    icon(node_32, () => "grip");
    reset(span_9);
    var span_10 = sibling(span_9, 2);
    var text_10 = child(span_10, true);
    reset(span_10);
    var node_33 = sibling(span_10, 2);
    {
      var consequent_16 = ($$anchor3) => {
        var input_4 = root_19();
        remove_input_defaults(input_4);
        bind_value(input_4, () => item().title, ($$value) => item().title = $$value);
        append($$anchor3, input_4);
      };
      if_block(node_33, ($$render) => {
        if (item().kind !== "row") $$render(consequent_16);
      });
    }
    var node_34 = sibling(node_33, 2);
    {
      var consequent_17 = ($$anchor3) => {
        var button_17 = root_20();
        var span_11 = child(button_17);
        let classes_3;
        var node_35 = child(span_11);
        icon(node_35, () => "chevron-down");
        reset(span_11);
        reset(button_17);
        template_effect(() => classes_3 = set_class(span_11, 1, "di-chevron svelte-10v1sym", null, classes_3, { collapsed: item().collapsed }));
        delegated("click", button_17, () => item().collapsed = !item().collapsed);
        append($$anchor3, button_17);
      };
      if_block(node_34, ($$render) => {
        if (item().kind === "section") $$render(consequent_17);
      });
    }
    var node_36 = sibling(node_34, 4);
    {
      var consequent_18 = ($$anchor3) => {
        var div_18 = root_22();
        var node_37 = sibling(child(div_18), 2);
        each(node_37, 16, () => [2, 3, 4], index, ($$anchor4, n) => {
          var button_18 = root_21();
          let classes_4;
          var text_11 = child(button_18, true);
          reset(button_18);
          template_effect(() => {
            classes_4 = set_class(button_18, 1, "di-col-btn svelte-10v1sym", null, classes_4, { active: item().columns === n });
            set_attribute2(button_18, "data-columns", n);
            set_text(text_11, n);
          });
          delegated("click", button_18, () => item().columns = n);
          append($$anchor4, button_18);
        });
        reset(div_18);
        append($$anchor3, div_18);
      };
      if_block(node_36, ($$render) => {
        if (item().kind === "row") $$render(consequent_18);
      });
    }
    var div_19 = sibling(node_36, 2);
    var node_38 = child(div_19);
    {
      var consequent_19 = ($$anchor3) => {
        var button_19 = root_23();
        var node_39 = child(button_19);
        icon(node_39, () => "more");
        reset(button_19);
        template_effect(() => set_attribute2(button_19, "title", item().kind === "row" ? "Convert to Group" : "Convert to Row"));
        delegated("click", button_19, () => convertContainer(item()));
        append($$anchor3, button_19);
      };
      if_block(node_38, ($$render) => {
        if (item().kind === "row" || item().kind === "group") $$render(consequent_19);
      });
    }
    var button_20 = sibling(node_38, 2);
    var node_40 = child(button_20);
    icon(node_40, () => "chevron-up");
    reset(button_20);
    var button_21 = sibling(button_20, 2);
    var node_41 = child(button_21);
    icon(node_41, () => "chevron-down");
    reset(button_21);
    var node_42 = sibling(button_21, 2);
    moveToMenu(node_42, item, parentItems, index2);
    var button_22 = sibling(node_42, 2);
    var node_43 = child(button_22);
    icon(node_43, () => "x");
    reset(button_22);
    reset(div_19);
    reset(div_17);
    var div_20 = sibling(div_17, 2);
    var node_44 = child(div_20);
    {
      var consequent_20 = ($$anchor3) => {
        var fragment_13 = root_24();
        var div_21 = first_child(fragment_13);
        each(div_21, 23, () => item().items, (child2) => child2._id, ($$anchor4, child2, ci) => {
          anyItem($$anchor4, () => get(child2), () => item().items, () => get(ci));
        });
        reset(div_21);
        var node_45 = sibling(div_21, 2);
        addMenu(node_45, () => item().items);
        template_effect(() => set_style(div_21, `grid-template-columns: repeat(${item().columns}, 1fr)`));
        append($$anchor3, fragment_13);
      };
      var alternate_1 = ($$anchor3) => {
        itemsList($$anchor3, () => item().items);
      };
      if_block(node_44, ($$render) => {
        if (item().kind === "row") $$render(consequent_20);
        else $$render(alternate_1, -1);
      });
    }
    reset(div_20);
    reset(div_16);
    template_effect(() => {
      classes_2 = set_class(div_16, 1, "di-container svelte-10v1sym", null, classes_2, { collapsed: item().kind === "section" && item().collapsed });
      set_attribute2(div_16, "data-item-kind", item().kind);
      set_class(span_10, 1, `chip ${chipClass() ?? ""}`, "svelte-10v1sym");
      set_text(text_10, chipLabel());
    });
    event("dragstart", span_9, (e) => handleItemDragStart(e, item()._id));
    event("dragend", span_9, handleItemDragEnd);
    delegated("click", button_20, () => moveItemAt(parentItems(), index2(), -1));
    delegated("click", button_21, () => moveItemAt(parentItems(), index2(), 1));
    delegated("click", button_22, () => removeItemAt(parentItems(), index2()));
    append($$anchor2, div_16);
  };
  let pluginId = prop($$props, "pluginId", 7, "comfyui-backend"), plugin = prop($$props, "plugin", 7, null);
  const API_BASE = `/api/plugins/${pluginId()}`;
  const EDIT_STORAGE_KEY = "comfyui-import-edit-preset-id";
  const LOCKED_ROLES = /* @__PURE__ */ new Set(["seed", "prompt_positive", "prompt_negative", "batch_size"]);
  const TRANSFORM_OPTIONS = [
    { value: "none", label: "None" },
    { value: "strip_model_prefix", label: "Strip model prefix" },
    { value: "split_wh_width", label: "Split W\xD7H \u2192 width" },
    { value: "split_wh_height", label: "Split W\xD7H \u2192 height" },
    { value: "seed", label: "Seed" }
  ];
  const TAB_ICON_OPTIONS = [
    { value: "", label: "None" },
    { value: "generation", label: "Generation" },
    { value: "settings", label: "Settings" },
    { value: "lora", label: "LoRA" },
    { value: "model", label: "Model" },
    { value: "image", label: "Image" },
    { value: "video", label: "Video" },
    { value: "film", label: "Film" },
    { value: "sparkles", label: "Sparkles" },
    { value: "embedding", label: "Embedding" },
    { value: "sliders", label: "Sliders" },
    { value: "face", label: "Face" },
    { value: "warning", label: "Warning" },
    { value: "information-circle", label: "Info" }
  ];
  const TAB_DISPLAY_OPTIONS = [
    { value: "icon_only", label: "Icon only" },
    { value: "icon_label", label: "Icon + label" },
    { value: "label", label: "Label only" }
  ];
  const FORMAT_OPTIONS = [
    { value: "as_is", label: "As-is" },
    { value: "number", label: "Number" },
    { value: "wxh", label: "W \xD7 H" },
    { value: "model_name", label: "Model name" },
    { value: "list", label: "List (count + names)" },
    { value: "jinja", label: "Custom Jinja" }
  ];
  let step = state(1);
  let editPresetId = state(null);
  let editLoading = state(false);
  let rawText = state("");
  let dragOver = state(false);
  let fileInputEl = state(null);
  let analyzing = state(false);
  let analyzeError = state("");
  let analysis = state(null);
  let workflowJson = state(null);
  let _uidCounter = 0;
  function uid2(prefix) {
    _uidCounter += 1;
    return `${prefix}_${_uidCounter}`;
  }
  function emptyForm() {
    return {
      tabs: [
        {
          id: "generation",
          label: "Generation",
          icon: null,
          icon_display: "label",
          items: []
        }
      ],
      lora_chain: null
    };
  }
  let form = state(proxy(emptyForm()));
  let loraPickerFieldName = state(null);
  let loraKeepFixed = state(proxy(/* @__PURE__ */ new Set()));
  let activeTabId = state("generation");
  let fieldTypeOptions = state(proxy([]));
  let families = state(proxy([]));
  let modelFamily = state("");
  let variant = state("imported");
  let displayName = state("");
  let leftSearch = state("");
  let renamingTabId = state(null);
  let tabPopoverId = state(null);
  let tabPopoverAnchorEl = state(null);
  let addMenuOpenFor = state(null);
  let addMenuAnchorEl = state(null);
  let expandedFieldId = state(null);
  let dismissedSuggestionIds = state(proxy({}));
  let initialHistoryDefault = state(proxy([]));
  let historyRows = state(proxy([]));
  let historyBuilt = state(false);
  let requirementsLoading = state(false);
  let requirementsError = state("");
  let requirementsResults = state(null);
  let creating = state(false);
  let createError = state("");
  let createResult = state(null);
  let activeTab = user_derived(() => get(form).tabs.find((t) => t.id === get(activeTabId)) || get(form).tabs[0]);
  let allFields = user_derived(() => collectFields(get(form)));
  let mappedKeySet = user_derived(() => new Set(get(allFields).flatMap((f) => f.mappings.map((m) => `${m.node_id}:${m.input_name}`))));
  let mappedFieldByKey = user_derived(() => new Map(get(allFields).flatMap((f) => f.mappings.map((m) => [`${m.node_id}:${m.input_name}`, f]))));
  let loraChainNodes = user_derived(() => get(analysis)?.lora_chain?.nodes || []);
  let loraReplacedIds = user_derived(() => new Set(get(form).lora_chain?.replaced_node_ids || []));
  let loraKeptIds = user_derived(() => new Set(get(form).lora_chain?.kept_node_ids || []));
  let loraConverted = user_derived(() => !!get(form).lora_chain && !!get(loraPickerFieldName) && get(allFields).some((f) => f.field_name === get(loraPickerFieldName) && f.field_type === "lora_picker"));
  let loraSandwichError = user_derived(() => get(loraConverted) ? null : findSandwichedLoraNode(get(loraChainNodes), get(loraKeepFixed)));
  let leftGroups = user_derived(() => buildLeftGroups((get(analysis)?.candidates || []).filter((c) => !get(loraReplacedIds).has(c.node_id)), get(leftSearch)));
  let mappableCandidates = user_derived(() => (get(analysis)?.candidates || []).filter((c) => !isLockedCandidate(c) && !get(loraReplacedIds).has(c.node_id)));
  let formFieldCount = user_derived(() => get(allFields).length);
  let canContinueForm = user_derived(() => !!get(modelFamily).trim() && !!get(displayName).trim());
  let enabledHistoryCount = user_derived(() => get(historyRows).filter((r) => r.enabled).length);
  let offHistoryCount = user_derived(() => get(historyRows).length - get(enabledHistoryCount));
  let requirementsOkCount = user_derived(() => get(requirementsResults) ? get(requirementsResults).filter((r) => r.status === "ok").length : 0);
  let requirementsMissingCount = user_derived(() => get(requirementsResults) ? get(requirementsResults).filter((r) => r.status !== "ok").length : 0);
  function candidateKey(c) {
    return `${c.node_id}:${c.input_name}`;
  }
  function isLockedCandidate(c) {
    return LOCKED_ROLES.has(c.role);
  }
  function normalizeMatchName(s) {
    return (s || "").toString().trim().toLowerCase().replace(/[-\s]+/g, "_");
  }
  function candidateNameMatchesField(c, field) {
    if (get(mappedKeySet).has(candidateKey(c))) return false;
    const normInput = normalizeMatchName(c.input_name);
    return normInput === normalizeMatchName(field.field_name) || normInput === normalizeMatchName(field.label);
  }
  function nameMatchSuggestionsFor(field) {
    if (field.mappings.length > 0) return [];
    return get(mappableCandidates).filter((c) => candidateNameMatchesField(c, field)).slice(0, 3);
  }
  function dismissSuggestion(fieldId) {
    set(dismissedSuggestionIds, { ...get(dismissedSuggestionIds), [fieldId]: true }, true);
  }
  function buildLeftGroups(candidates, search) {
    const q = search.trim().toLowerCase();
    const byNode = /* @__PURE__ */ new Map();
    for (const c of candidates) {
      if (q && !`${c.node_title || ""} ${c.class_type || ""} ${c.input_name}`.toLowerCase().includes(q)) continue;
      if (!byNode.has(c.node_id)) {
        byNode.set(c.node_id, {
          node_id: c.node_id,
          node_title: c.node_title || c.class_type,
          class_type: c.class_type,
          rows: []
        });
      }
      byNode.get(c.node_id).rows.push(c);
    }
    return [...byNode.values()];
  }
  function parseWh(value) {
    const m = typeof value === "string" ? value.match(/^(\d+)\D+(\d+)$/) : null;
    return m ? { width: Number(m[1]), height: Number(m[2]) } : null;
  }
  function whToDefault(wh) {
    return `${wh.width ?? 0}x${wh.height ?? 0}`;
  }
  function hydrateItem(it) {
    const _id = uid2("item");
    if (it.kind === "row") return {
      _id,
      kind: "row",
      columns: it.columns || 2,
      items: (it.items || []).map(hydrateItem)
    };
    if (it.kind === "group") return {
      _id,
      kind: "group",
      title: it.title || "Group",
      items: (it.items || []).map(hydrateItem)
    };
    if (it.kind === "section") return {
      _id,
      kind: "section",
      title: it.title || "Section",
      collapsed: !!it.collapsed,
      items: (it.items || []).map(hydrateItem)
    };
    if (it.kind === "header") return { _id, kind: "header", text: it.text || "" };
    const field = {
      _id,
      kind: "field",
      field_name: it.field_name,
      field_type: it.field_type || "text",
      label: it.label || it.field_name,
      default: it.default ?? null,
      config: it.config ?? null,
      mappings: (it.mappings || []).map((m) => ({ ...m }))
    };
    if (field.field_type === "resolution") {
      const wh = parseWh(field.default);
      if (wh) field._wh = wh;
    }
    return field;
  }
  function hydrateTab(t) {
    const icon2 = t.icon ?? null;
    const icon_display = t.icon_display ?? (icon2 ? "icon_only" : "label");
    return {
      id: t.id || uid2("tab"),
      label: t.label || "Tab",
      icon: icon2,
      icon_display,
      items: (t.items || []).map(hydrateItem)
    };
  }
  function hydrateForm(raw) {
    const tabs = raw?.tabs?.length ? raw.tabs.map(hydrateTab) : emptyForm().tabs;
    const lora_chain = raw?.lora_chain ? {
      replaced_node_ids: [...raw.lora_chain.replaced_node_ids || []],
      kept_node_ids: [...raw.lora_chain.kept_node_ids || []]
    } : null;
    return { tabs, lora_chain };
  }
  function dehydrateItem(it) {
    if (it.kind === "row") return {
      kind: "row",
      columns: it.columns,
      items: it.items.map(dehydrateItem)
    };
    if (it.kind === "group") return {
      kind: "group",
      title: it.title,
      items: it.items.map(dehydrateItem)
    };
    if (it.kind === "section") return {
      kind: "section",
      title: it.title,
      collapsed: it.collapsed,
      items: it.items.map(dehydrateItem)
    };
    if (it.kind === "header") return { kind: "header", text: it.text };
    return {
      kind: "field",
      field_name: it.field_name,
      field_type: it.field_type,
      label: it.label,
      default: it.default ?? null,
      config: it.config ?? null,
      mappings: it.mappings.map((m) => ({
        node_id: m.node_id,
        input_name: m.input_name,
        transform: m.transform || "none"
      }))
    };
  }
  function dehydrateForm() {
    return {
      tabs: get(form).tabs.map((t) => ({
        id: t.id,
        label: t.label,
        icon: t.icon ?? null,
        icon_display: t.icon_display || (t.icon ? "icon_only" : "label"),
        items: t.items.map(dehydrateItem)
      })),
      lora_chain: get(form).lora_chain ? {
        replaced_node_ids: [...get(form).lora_chain.replaced_node_ids],
        kept_node_ids: [...get(form).lora_chain.kept_node_ids]
      } : null
    };
  }
  function collectFieldsFromItems(items, acc) {
    for (const it of items) {
      if (it.kind === "field") acc.push(it);
      else if (it.items) collectFieldsFromItems(it.items, acc);
    }
    return acc;
  }
  function collectFields(f) {
    const acc = [];
    for (const t of f.tabs) collectFieldsFromItems(t.items, acc);
    return acc;
  }
  function initializeFormAndHistory(payload) {
    set(form, hydrateForm(payload.form || payload.default_form || emptyForm()), true);
    set(activeTabId, get(form).tabs[0]?.id || "generation", true);
    set(initialHistoryDefault, payload.history || payload.default_history || [], true);
    set(historyRows, [], true);
    set(historyBuilt, false);
    if (get(form).lora_chain) {
      const pickerField = collectFields(get(form)).find((f) => f.field_type === "lora_picker");
      set(loraPickerFieldName, pickerField ? pickerField.field_name : null, true);
      set(loraKeepFixed, new Set(get(form).lora_chain.kept_node_ids || []), true);
      if (!get(
        loraPickerFieldName
        // stale/incomplete sidecar - start clean
      )) get(form).lora_chain = null;
    } else {
      set(loraPickerFieldName, null);
      set(loraKeepFixed, /* @__PURE__ */ new Set(), true);
    }
  }
  function uniqueFieldName(base) {
    let name = base || "field";
    let n = 1;
    while (get(allFields).some((f) => f.field_name === name)) {
      n += 1;
      name = `${base}_${n}`;
    }
    return name;
  }
  function findFieldByName(name) {
    return get(allFields).find((f) => f.field_name === name) || null;
  }
  function defaultTransformFor(c) {
    if (c.suggested_field_type === "resolution") return c.input_name === "width" ? "split_wh_width" : c.input_name === "height" ? "split_wh_height" : "none";
    if (c.role === "seed") return "seed";
    return "none";
  }
  function addCandidateToForm(c) {
    const key2 = candidateKey(c);
    if (get(mappedKeySet).has(key2)) return;
    const name = c.suggested_field_name || c.input_name;
    const existing = findFieldByName(name);
    if (existing) {
      existing.mappings.push({
        node_id: c.node_id,
        input_name: c.input_name,
        transform: defaultTransformFor(c)
      });
      if (c.suggested_field_type === "resolution") {
        const wh = { ...existing._wh || {}, [c.input_name]: c.current_value };
        existing._wh = wh;
        existing.default = whToDefault(wh);
      }
      return;
    }
    const tab = get(form).tabs.find((t) => t.id === get(activeTabId)) || get(form).tabs[0];
    if (!tab) return;
    const field = {
      _id: uid2("item"),
      kind: "field",
      field_name: uniqueFieldName(name),
      field_type: c.suggested_field_type || "text",
      label: c.suggested_label || c.input_name,
      default: c.suggested_field_type === "resolution" ? whToDefault({ [c.input_name]: c.current_value }) : c.current_value ?? null,
      config: c.suggested_config ?? null,
      mappings: [
        {
          node_id: c.node_id,
          input_name: c.input_name,
          transform: defaultTransformFor(c)
        }
      ]
    };
    if (c.suggested_field_type === "resolution") field._wh = { [c.input_name]: c.current_value };
    tab.items.push(field);
  }
  function toggleLoraKeepFixed(nodeId) {
    if (get(loraConverted)) return;
    const next2 = new Set(get(loraKeepFixed));
    if (next2.has(nodeId)) next2.delete(nodeId);
    else next2.add(nodeId);
    set(loraKeepFixed, next2, true);
  }
  function findSandwichedLoraNode(nodes, keepFixedSet) {
    const order = nodes.map((n) => n.node_id);
    for (let i = 0; i < order.length; i++) {
      const nodeId = order[i];
      if (!keepFixedSet.has(nodeId)) continue;
      let beforeId = null;
      for (let j = i - 1; j >= 0; j--) {
        if (!keepFixedSet.has(order[j])) {
          beforeId = order[j];
          break;
        }
      }
      let afterId = null;
      for (let j = i + 1; j < order.length; j++) {
        if (!keepFixedSet.has(order[j])) {
          afterId = order[j];
          break;
        }
      }
      if (beforeId !== null && afterId !== null) return { nodeId, beforeId, afterId };
    }
    return null;
  }
  function applyLoraPickerConversion(tab, keepFixedIds, fieldNameHint) {
    const nodes = get(loraChainNodes);
    if (!tab || !nodes.length || get(loraConverted)) return false;
    if (findSandwichedLoraNode(nodes, keepFixedIds)) return false;
    const replaced = nodes.filter((n) => !keepFixedIds.has(n.node_id));
    const kept = nodes.filter((n) => keepFixedIds.has(n.node_id));
    const fieldName = uniqueFieldName(fieldNameHint || "loras");
    const field = {
      _id: uid2("item"),
      kind: "field",
      field_name: fieldName,
      field_type: "lora_picker",
      label: "LoRAs",
      default: replaced.map((n) => ({
        model: `models/loras/${n.lora_name || ""}`,
        strength: typeof n.strength_model === "number" ? n.strength_model : 1
      })),
      config: {
        model_type: "lora",
        placeholder: "Select a LoRA...",
        allow_info_modal: true,
        strength_min: -2,
        strength_max: 2,
        strength_step: 0.1,
        strength_default: 1,
        max_items: 6
      },
      mappings: []
    };
    tab.items.push(field);
    get(form).lora_chain = {
      replaced_node_ids: replaced.map((n) => n.node_id),
      kept_node_ids: kept.map((n) => n.node_id)
    };
    set(loraPickerFieldName, fieldName, true);
    return true;
  }
  function convertLoraChainToPicker() {
    applyLoraPickerConversion(get(activeTab) || get(form).tabs[0], get(loraKeepFixed), "loras");
  }
  user_effect(() => {
    if (get(loraPickerFieldName) && !get(allFields).some((f) => f.field_name === get(loraPickerFieldName))) {
      get(form).lora_chain = null;
      set(loraPickerFieldName, null);
      set(loraKeepFixed, /* @__PURE__ */ new Set(), true);
    }
  });
  function addTab() {
    let n = get(form).tabs.length + 1;
    let id = `tab_${n}`;
    while (get(form).tabs.some((t) => t.id === id)) {
      n += 1;
      id = `tab_${n}`;
    }
    get(form).tabs.push({
      id,
      label: `Tab ${get(form).tabs.length + 1}`,
      icon: null,
      icon_display: "label",
      items: []
    });
    set(activeTabId, id, true);
  }
  function setTabIcon(tab, value) {
    const icon2 = value || null;
    if (!icon2) tab.icon_display = "label";
    else if (tab.icon_display === "label") tab.icon_display = "icon_only";
    tab.icon = icon2;
  }
  function setTabDisplay(tab, value) {
    tab.icon_display = value;
  }
  function moveTab(index2, dir) {
    const j = index2 + dir;
    if (j < 0 || j >= get(form).tabs.length) return;
    const [t] = get(form).tabs.splice(index2, 1);
    get(form).tabs.splice(j, 0, t);
  }
  function deleteTab(index2) {
    if (get(form).tabs.length <= 1) return;
    const [removed] = get(form).tabs.splice(index2, 1);
    if (get(activeTabId) === removed.id) set(activeTabId, get(form).tabs[Math.max(0, index2 - 1)].id, true);
  }
  function toggleTabPopover(tabId, anchorEl) {
    if (get(tabPopoverId) === tabId) {
      set(tabPopoverId, null);
    } else {
      set(tabPopoverId, tabId, true);
      set(tabPopoverAnchorEl, anchorEl, true);
    }
  }
  function moveItemAt(items, index2, dir) {
    const j = index2 + dir;
    if (j < 0 || j >= items.length) return;
    const [it] = items.splice(index2, 1);
    items.splice(j, 0, it);
  }
  function removeItemAt(items, index2) {
    items.splice(index2, 1);
  }
  function locateItemInForm(id) {
    function search(items) {
      for (let i = 0; i < items.length; i++) {
        if (items[i]._id === id) return { items, index: i };
        if (items[i].items) {
          const found = search(items[i].items);
          if (found) return found;
        }
      }
      return null;
    }
    for (const t of get(form).tabs) {
      const found = search(t.items);
      if (found) return found;
    }
    return null;
  }
  function moveItemToTab(parentItems, index2, targetTabId) {
    const targetTab = get(form).tabs.find((t) => t.id === targetTabId);
    if (!targetTab || parentItems === targetTab.items) return;
    const [it] = parentItems.splice(index2, 1);
    targetTab.items.push(it);
    set(activeTabId, targetTabId, true);
  }
  let moveToPopoverId = state(null);
  let moveToPopoverAnchorEl = state(null);
  function toggleMoveToPopover(itemId, anchorEl) {
    if (get(moveToPopoverId) === itemId) {
      set(moveToPopoverId, null);
    } else {
      set(moveToPopoverId, itemId, true);
      set(moveToPopoverAnchorEl, anchorEl, true);
    }
  }
  let draggedItemId = state(null);
  let dragOverTabId = state(null);
  function handleItemDragStart(e, itemId) {
    set(draggedItemId, itemId, true);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", itemId);
  }
  function handleItemDragEnd() {
    set(draggedItemId, null);
  }
  function handleTabDragOver(e, tabId) {
    if (!get(draggedItemId)) return;
    e.preventDefault();
    set(dragOverTabId, tabId, true);
  }
  function handleTabDragLeave(tabId) {
    if (get(dragOverTabId) === tabId) set(dragOverTabId, null);
  }
  function handleTabDrop(e, tabId) {
    e.preventDefault();
    const id = get(draggedItemId) || e.dataTransfer.getData("text/plain");
    set(dragOverTabId, null);
    set(draggedItemId, null);
    if (!id) return;
    const loc = locateItemInForm(id);
    if (loc) moveItemToTab(loc.items, loc.index, tabId);
  }
  function addItemToContainer(items, kind) {
    if (kind === "field") items.push({
      _id: uid2("item"),
      kind: "field",
      field_name: uniqueFieldName("field"),
      field_type: "text",
      label: "New field",
      default: null,
      config: null,
      mappings: []
    });
    else if (kind === "row") items.push({ _id: uid2("item"), kind: "row", columns: 2, items: [] });
    else if (kind === "group") items.push({ _id: uid2("item"), kind: "group", title: "Group", items: [] });
    else if (kind === "section") items.push({
      _id: uid2("item"),
      kind: "section",
      title: "Section",
      collapsed: false,
      items: []
    });
    else if (kind === "header") items.push({ _id: uid2("item"), kind: "header", text: "Header" });
    set(addMenuOpenFor, null);
  }
  function toggleAddMenu(items, anchorEl) {
    if (get(addMenuOpenFor) === items) {
      set(addMenuOpenFor, null);
    } else {
      set(addMenuOpenFor, items, true);
      set(addMenuAnchorEl, anchorEl, true);
    }
  }
  function convertContainer(item) {
    if (item.kind === "row") {
      item.kind = "group";
      item.title = "Group";
      delete item.columns;
    } else if (item.kind === "group") {
      item.kind = "row";
      item.columns = 2;
      delete item.title;
    }
  }
  function toggleMapping(field, c, checked) {
    const idx = field.mappings.findIndex((m) => m.node_id === c.node_id && m.input_name === c.input_name);
    if (checked && idx === -1) field.mappings.push({
      node_id: c.node_id,
      input_name: c.input_name,
      transform: defaultTransformFor(c)
    });
    else if (!checked && idx !== -1) field.mappings.splice(idx, 1);
  }
  function setMappingTransform(field, c, value) {
    const m = field.mappings.find((m2) => m2.node_id === c.node_id && m2.input_name === c.input_name);
    if (m) m.transform = value;
  }
  function serializeImportItem(it) {
    if (it.kind === "field") {
      return {
        kind: "field",
        field_name: it.field_name,
        label: it.label,
        field_type: it.field_type,
        mappings: (it.mappings || []).map((m) => ({
          node_id: m.node_id,
          input_name: m.input_name,
          transform: m.transform || "none"
        }))
      };
    }
    if (it.kind === "header") return { kind: "header", text: it.text };
    return {
      kind: it.kind,
      label: it.title ?? null,
      items: (it.items || []).map(serializeImportItem)
    };
  }
  function buildImportChatContext() {
    if (!get(analysis)) return null;
    return {
      workflow_name: get(displayName) || "",
      format: get(analysis).format,
      node_count: get(analysis).node_count,
      candidates: (get(analysis).candidates || []).map((c) => ({
        node_id: c.node_id,
        class_type: c.class_type,
        node_title: c.node_title,
        input_name: c.input_name,
        current_value: c.current_value,
        value_type: c.value_type,
        suggested_field_type: c.suggested_field_type,
        role: c.role,
        locked: isLockedCandidate(c)
      })),
      form: {
        tabs: get(form).tabs.map((tab) => ({
          id: tab.id,
          label: tab.label,
          items: tab.items.map(serializeImportItem)
        }))
      },
      mapped: get(allFields).flatMap((f) => f.mappings.map((m) => ({
        field_name: f.field_name,
        node_id: m.node_id,
        input_name: m.input_name,
        transform: m.transform || "none"
      }))),
      lora_chain: get(loraChainNodes).length ? {
        nodes: get(loraChainNodes).map((n) => ({
          node_id: n.node_id,
          class_type: n.class_type,
          lora_name: n.lora_name,
          strength_model: n.strength_model
        })),
        replaced: [...get(loraReplacedIds)],
        kept: [...get(loraKeptIds)]
      } : null
    };
  }
  function slugifyImportTabId(label) {
    const slug = (label || "").replace(/[^A-Za-z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase();
    return slug || "tab";
  }
  function findImportTab(ref) {
    if (!ref) return null;
    return get(form).tabs.find((t) => t.id === ref) || get(form).tabs.find((t) => (t.label || "").toLowerCase() === String(ref).toLowerCase()) || null;
  }
  function tabIdForField(field) {
    for (const t of get(form).tabs) {
      if (collectFieldsFromItems(t.items, []).includes(field)) return t.id;
    }
    return null;
  }
  function applyImportMapping(field, nodeId, inputName, transform) {
    if (!nodeId || !inputName) {
      console.warn("propose_form_changes: mapping missing node_id/input_name", { field: field?.field_name, nodeId, inputName });
      return;
    }
    const candidate = (get(analysis)?.candidates || []).find((c) => c.node_id === nodeId && c.input_name === inputName);
    if (!candidate) {
      console.warn(`propose_form_changes: no such candidate ${nodeId}.${inputName}`);
      return;
    }
    if (isLockedCandidate(candidate)) {
      console.warn(`propose_form_changes: skipping locked candidate ${nodeId}.${inputName}`);
      return;
    }
    const owner = get(mappedFieldByKey).get(candidateKey(candidate));
    if (owner && owner !== field) {
      console.warn(`propose_form_changes: ${nodeId}.${inputName} is already mapped to '${owner.field_name}'`);
      return;
    }
    toggleMapping(field, candidate, true);
    if (transform && transform !== "none") setMappingTransform(field, candidate, transform);
  }
  function applyImportFormChanges(result) {
    const ops = result?.ops;
    if (!Array.isArray(ops) || ops.length === 0) return;
    let lastTabId = null;
    let applied = 0;
    for (const op of ops) {
      if (!op || typeof op !== "object") continue;
      if (op.op === "add_tab") {
        const label = op.label || "Tab";
        const baseId = op.id || slugifyImportTabId(label);
        let id = baseId;
        let n = 2;
        while (get(form).tabs.some((t) => t.id === id)) {
          id = `${baseId}_${n}`;
          n += 1;
        }
        get(form).tabs.push({ id, label, icon: null, items: [] });
        lastTabId = id;
        applied += 1;
      } else if (op.op === "add_field") {
        const tab = findImportTab(op.tab) || get(activeTab) || get(form).tabs[0];
        if (!tab) {
          console.warn("propose_form_changes: add_field has no target tab", op);
          continue;
        }
        addItemToContainer(tab.items, "field");
        const created = tab.items[tab.items.length - 1];
        created.field_name = op.field_name && !get(allFields).some((f) => f !== created && f.field_name === op.field_name) ? op.field_name : uniqueFieldName(op.field_name || "field");
        created.field_type = op.field_type || "text";
        created.label = op.label || created.field_name;
        if ("default" in op) created.default = op.default ?? null;
        for (const m of op.mappings || []) applyImportMapping(created, m.node_id, m.input_name, m.transform);
        lastTabId = tab.id;
        applied += 1;
      } else if (op.op === "map") {
        const field = findFieldByName(op.field_name);
        if (!field) {
          console.warn(`propose_form_changes: map references unknown field '${op.field_name}'`, op);
          continue;
        }
        applyImportMapping(field, op.node_id, op.input_name, op.transform);
        lastTabId = tabIdForField(field) || lastTabId;
        applied += 1;
      } else if (op.op === "lora_picker") {
        const tab = findImportTab(op.tab) || get(activeTab) || get(form).tabs[0];
        if (!tab) {
          console.warn("propose_form_changes: lora_picker has no target tab", op);
          continue;
        }
        const keepFixed = new Set(Array.isArray(op.keep_fixed) ? op.keep_fixed : []);
        if (applyLoraPickerConversion(tab, keepFixed, op.field_name || "loras")) {
          lastTabId = tab.id;
          applied += 1;
        } else {
          console.warn("propose_form_changes: lora_picker could not be applied (no chain, or already converted)", op);
        }
      } else {
        console.warn("propose_form_changes: unknown op", op);
      }
    }
    if (applied === 0) return;
    if (get(step) < 2) set(step, 2);
    if (lastTabId) set(activeTabId, lastTabId, true);
    window.__potionui?.notifications?.toast?.("success", `Applied ${applied} change${applied === 1 ? "" : "s"} from the assistant`);
  }
  user_effect(() => {
    const chat = window.__potionui?.chat;
    if (!chat || get(step) < 2 || !get(analysis)) return;
    const unregisterContext = chat.provideContext("comfyui_import", buildImportChatContext);
    const unregisterMode = chat.declareMode("comfyui-import");
    const unregisterTool = chat.onToolApplied("propose_form_changes", applyImportFormChanges);
    return () => {
      unregisterContext();
      unregisterMode();
      unregisterTool();
    };
  });
  function fieldTypeOptionsFor(current) {
    return [.../* @__PURE__ */ new Set([current, ...get(fieldTypeOptions)])].filter(Boolean);
  }
  function displayDefault(item) {
    if (item._wh) return `${item._wh.width ?? ""} \xD7 ${item._wh.height ?? ""}`;
    const d = item.default;
    if (Array.isArray(d)) return `${d.length} item${d.length === 1 ? "" : "s"}`;
    return d === null || d === void 0 ? "" : String(d);
  }
  function setDefaultFromText(item, text2) {
    if (item.field_type === "resolution") {
      const m = text2.match(/(-?\d+)\D+(-?\d+)/);
      if (m) {
        item._wh = { width: Number(m[1]), height: Number(m[2]) };
        item.default = whToDefault(item._wh);
      }
      return;
    }
    item.default = text2;
  }
  function buildHistoryRows(defaultHistory) {
    const byName = new Map((defaultHistory || []).map((h, i) => [h.field, { ...h, _order: i }]));
    const matched = [];
    const unmatched = [];
    for (const f of get(allFields)) {
      const d = byName.get(f.field_name);
      if (d) matched.push({
        field_name: f.field_name,
        label: d.label ?? f.label,
        format: d.format || "as_is",
        template: d.template ?? null,
        enabled: true,
        _order: d._order
      });
      else unmatched.push({
        field_name: f.field_name,
        label: f.label,
        format: "as_is",
        template: null,
        enabled: false
      });
    }
    matched.sort((a, b) => a._order - b._order);
    set(historyRows, [...matched, ...unmatched].map(({ _order, ...r }) => r), true);
  }
  function syncHistoryRows() {
    const existingByName = new Map(get(historyRows).map((r) => [r.field_name, r]));
    const next2 = [];
    for (const f of get(allFields)) {
      const prev = existingByName.get(f.field_name);
      next2.push(prev || {
        field_name: f.field_name,
        label: f.label,
        format: "as_is",
        template: null,
        enabled: false
      });
    }
    set(historyRows, next2, true);
  }
  function goToHistory() {
    if (!get(historyBuilt)) {
      buildHistoryRows(get(initialHistoryDefault));
      set(historyBuilt, true);
    } else {
      syncHistoryRows();
    }
    set(step, 3);
  }
  function fieldForRow(row) {
    return get(allFields).find((f) => f.field_name === row.field_name) || null;
  }
  function moveHistoryRow(index2, dir) {
    const j = index2 + dir;
    if (j < 0 || j >= get(historyRows).length) return;
    const [r] = get(historyRows).splice(index2, 1);
    get(historyRows).splice(j, 0, r);
  }
  function onFormatChange(row, value) {
    row.format = value;
    if (value === "jinja" && !row.template) row.template = `{{ form.${row.field_name} }}`;
  }
  function formatPreviewValue(field, format, template) {
    const raw = field?.default;
    const wh = field?._wh || parseWh(raw);
    if (format === "jinja") {
      const base = wh ? `${wh.width ?? ""} \xD7 ${wh.height ?? ""}` : String(raw ?? "");
      return (template || "").replace(/\{\{\s*form\.([a-zA-Z0-9_]+)\s*\}\}/g, (_m, name) => field && name === field.field_name ? base : "");
    }
    if (format === "wxh") {
      if (wh) return `${wh.width ?? ""} \xD7 ${wh.height ?? ""}`;
      return String(raw ?? "");
    }
    if (format === "model_name") {
      return String(raw ?? "").split("/").pop().split("\\").pop();
    }
    if (format === "list") {
      if (Array.isArray(raw)) return `${raw.length} item${raw.length === 1 ? "" : "s"}`;
      return String(raw ?? "");
    }
    return String(raw ?? "");
  }
  function authHeaders() {
    const token = typeof localStorage !== "undefined" ? localStorage.getItem("auth_token") : null;
    return token ? { Authorization: `Bearer ${token}` } : {};
  }
  onMount(() => {
    (async () => {
      try {
        const res = await fetch("/api/fields/types", { credentials: "include", headers: authHeaders() });
        if (res.ok) {
          const payload = await res.json();
          const list = payload?.data ?? [];
          set(
            fieldTypeOptions,
            [
              ...new Set(list.filter((t) => !t.container).map((t) => t.type))
            ].sort(),
            true
          );
        }
      } catch (e) {
      }
      try {
        const res = await fetch(`${API_BASE}/presets/families`, { credentials: "include", headers: authHeaders() });
        if (res.ok) {
          const payload = await res.json();
          set(families, payload?.families ?? [], true);
        }
      } catch (e) {
      }
    })();
    let pendingEditId = null;
    try {
      pendingEditId = sessionStorage.getItem(EDIT_STORAGE_KEY);
      if (pendingEditId) sessionStorage.removeItem(EDIT_STORAGE_KEY);
    } catch (e) {
    }
    if (pendingEditId) startEdit(pendingEditId);
  });
  async function startEdit(presetId) {
    set(editPresetId, presetId, true);
    set(editLoading, true);
    set(analyzeError, "");
    try {
      const res = await fetch(`${API_BASE}/presets/imported/${presetId}/source`, { credentials: "include", headers: authHeaders() });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        set(analyzeError, payload?.detail || payload?.message || `Could not load this preset (${res.status})`, true);
        set(editPresetId, null);
        return;
      }
      set(workflowJson, payload.workflow, true);
      set(analysis, payload, true);
      set(modelFamily, payload.model_family || "", true);
      set(variant, payload.variant || "imported", true);
      set(displayName, payload.display_name || "", true);
      initializeFormAndHistory(payload);
      set(step, 1);
    } catch (e) {
      set(analyzeError, "Could not reach the server.");
      set(editPresetId, null);
    } finally {
      set(editLoading, false);
    }
  }
  function readFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
      set(rawText, e.target?.result ?? "", true);
      set(analyzeError, "");
    };
    reader.readAsText(file);
  }
  function handleFileInput(e) {
    const file = e.target.files?.[0];
    if (file) readFile(file);
  }
  function handleDrop(e) {
    e.preventDefault();
    set(dragOver, false);
    const file = e.dataTransfer?.files?.[0];
    if (file) readFile(file);
  }
  function handleDragOver(e) {
    e.preventDefault();
    set(dragOver, true);
  }
  function handleDragLeave() {
    set(dragOver, false);
  }
  function handleSourceContinue() {
    if (get(analysis) && get(workflowJson)) {
      set(step, 2);
      return;
    }
    runAnalyze();
  }
  async function runAnalyze() {
    if (get(analyzing)) return;
    set(analyzeError, "");
    let parsed;
    try {
      parsed = JSON.parse(get(rawText));
    } catch (e) {
      set(analyzeError, "That is not valid JSON.");
      return;
    }
    set(analyzing, true);
    try {
      const res = await fetch(`${API_BASE}/presets/import/analyze`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ workflow: parsed })
      });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        set(analyzeError, payload?.detail || payload?.message || `Analyze failed (${res.status})`, true);
        return;
      }
      set(workflowJson, parsed, true);
      set(analysis, payload, true);
      initializeFormAndHistory(payload);
      set(step, 2);
    } catch (e) {
      set(analyzeError, "Could not reach the server.");
    } finally {
      set(analyzing, false);
    }
  }
  function changeWorkflow() {
    set(step, 1);
    set(analysis, null);
    set(workflowJson, null);
    set(form, emptyForm(), true);
    set(activeTabId, "generation");
    set(historyRows, [], true);
    set(historyBuilt, false);
    set(initialHistoryDefault, [], true);
    set(requirementsResults, null);
    set(requirementsError, "");
    set(createResult, null);
    set(createError, "");
  }
  async function goToRequirementsStep() {
    set(step, 4);
    if (get(requirementsResults) || get(requirementsLoading)) return;
    await runRequirementsPreview();
  }
  async function runRequirementsPreview() {
    set(requirementsLoading, true);
    set(requirementsError, "");
    try {
      const res = await fetch(`${API_BASE}/presets/import/requirements`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ workflow: get(workflowJson) })
      });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        set(requirementsError, payload?.detail || payload?.message || `Requirements check failed (${res.status})`, true);
        return;
      }
      set(requirementsResults, payload.results ?? [], true);
    } catch (e) {
      set(requirementsError, "Could not reach the server.");
    } finally {
      set(requirementsLoading, false);
    }
  }
  function historyPayload() {
    return get(historyRows).filter((r) => r.enabled).map((r) => ({
      field: r.field_name,
      label: r.label,
      format: r.format,
      template: r.format === "jinja" ? r.template || "" : null
    }));
  }
  async function runCreate() {
    if (!get(analysis) || !get(workflowJson) || get(creating)) return;
    set(createError, "");
    set(creating, true);
    try {
      const res = await fetch(`${API_BASE}/presets/import`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({
          workflow: get(workflowJson),
          model_family: get(modelFamily).trim(),
          variant: get(variant).trim() || "imported",
          display_name: get(displayName).trim(),
          overwrite_preset_id: get(editPresetId) || void 0,
          form: dehydrateForm(),
          history: historyPayload()
        })
      });
      const payload = await res.json().catch(() => null);
      if (!res.ok) {
        set(createError, payload?.detail || payload?.message || `Import failed (${res.status})`, true);
        return;
      }
      set(createResult, payload, true);
      set(step, 5);
    } catch (e) {
      set(createError, "Could not reach the server.");
    } finally {
      set(creating, false);
    }
  }
  function goBack() {
    if (get(step) > 1) set(step, get(step) - 1);
  }
  function importAnother() {
    set(step, 1);
    set(editPresetId, null);
    set(editLoading, false);
    set(rawText, "");
    set(analyzeError, "");
    set(analysis, null);
    set(workflowJson, null);
    set(form, emptyForm(), true);
    set(activeTabId, "generation");
    set(leftSearch, "");
    set(renamingTabId, null);
    set(tabPopoverId, null);
    set(addMenuOpenFor, null);
    set(expandedFieldId, null);
    set(modelFamily, "");
    set(variant, "imported");
    set(displayName, "");
    set(historyRows, [], true);
    set(historyBuilt, false);
    set(initialHistoryDefault, [], true);
    set(requirementsLoading, false);
    set(requirementsError, "");
    set(requirementsResults, null);
    set(creating, false);
    set(createError, "");
    set(createResult, null);
  }
  function stepState(index2) {
    if (index2 < get(step)) return "done";
    if (index2 === get(step)) return "current";
    return "upcoming";
  }
  var $$exports = {
    get pluginId() {
      return pluginId();
    },
    set pluginId($$value = "comfyui-backend") {
      pluginId($$value);
      flushSync();
    },
    get plugin() {
      return plugin();
    },
    set plugin($$value = null) {
      plugin($$value);
      flushSync();
    },
    $set: update_legacy_props,
    $on: ($$event_name, $$event_cb) => add_legacy_event_listener($$props, $$event_name, $$event_cb)
  };
  var fragment_16 = root_80();
  var div_22 = sibling(first_child(fragment_16), 2);
  var div_23 = child(div_22);
  var div_24 = child(div_23);
  var div_25 = child(div_24);
  var node_46 = child(div_25);
  stepDot(node_46, () => 1);
  reset(div_25);
  var div_26 = sibling(div_25, 2);
  var node_47 = sibling(child(div_26), 2);
  {
    var consequent_21 = ($$anchor2) => {
      var div_27 = root_26();
      var text_12 = child(div_27);
      reset(div_27);
      template_effect(() => set_text(text_12, `export (api) \xB7 ${get(analysis).node_count ?? ""} nodes`));
      append($$anchor2, div_27);
    };
    if_block(node_47, ($$render) => {
      if (get(analysis)) $$render(consequent_21);
    });
  }
  reset(div_26);
  reset(div_24);
  var div_28 = sibling(div_24, 2);
  var div_29 = child(div_28);
  var node_48 = child(div_29);
  stepDot(node_48, () => 2);
  reset(div_29);
  var div_30 = sibling(div_29, 2);
  var div_31 = sibling(child(div_30), 2);
  var text_13 = child(div_31, true);
  reset(div_31);
  reset(div_30);
  reset(div_28);
  var div_32 = sibling(div_28, 2);
  var div_33 = child(div_32);
  var node_49 = child(div_33);
  stepDot(node_49, () => 3);
  reset(div_33);
  var div_34 = sibling(div_33, 2);
  var node_50 = sibling(child(div_34), 2);
  {
    var consequent_22 = ($$anchor2) => {
      var div_35 = root_26();
      var text_14 = child(div_35);
      reset(div_35);
      template_effect(() => set_text(text_14, `${get(enabledHistoryCount) ?? ""} recorded${get(offHistoryCount) > 0 ? ` \xB7 ${get(offHistoryCount)} off` : ""}`));
      append($$anchor2, div_35);
    };
    if_block(node_50, ($$render) => {
      if (get(historyBuilt)) $$render(consequent_22);
    });
  }
  reset(div_34);
  reset(div_32);
  var div_36 = sibling(div_32, 2);
  var div_37 = child(div_36);
  var node_51 = child(div_37);
  stepDot(node_51, () => 4);
  reset(div_37);
  var div_38 = sibling(div_37, 2);
  var node_52 = sibling(child(div_38), 2);
  {
    var consequent_23 = ($$anchor2) => {
      var div_39 = root_27();
      append($$anchor2, div_39);
    };
    var consequent_24 = ($$anchor2) => {
      var div_40 = root_26();
      var text_15 = child(div_40);
      reset(div_40);
      template_effect(() => set_text(text_15, `${get(requirementsOkCount) ?? ""} ok \xB7 ${get(requirementsMissingCount) ?? ""} missing`));
      append($$anchor2, div_40);
    };
    if_block(node_52, ($$render) => {
      if (get(requirementsLoading)) $$render(consequent_23);
      else if (get(requirementsResults)) $$render(consequent_24, 1);
    });
  }
  reset(div_38);
  reset(div_36);
  var div_41 = sibling(div_36, 2);
  var div_42 = child(div_41);
  var node_53 = child(div_42);
  stepDot(node_53, () => 5);
  reset(div_42);
  next(2);
  reset(div_41);
  reset(div_23);
  var div_43 = sibling(div_23, 2);
  var div_44 = child(div_43);
  var node_54 = child(div_44);
  {
    var consequent_25 = ($$anchor2) => {
      var div_45 = root_28();
      var strong = sibling(child(div_45));
      var text_16 = child(strong, true);
      reset(strong);
      next();
      reset(div_45);
      template_effect(() => set_text(text_16, get(displayName) || "this preset"));
      append($$anchor2, div_45);
    };
    if_block(node_54, ($$render) => {
      if (get(editPresetId)) $$render(consequent_25);
    });
  }
  var node_55 = sibling(node_54, 2);
  {
    var consequent_30 = ($$anchor2) => {
      var fragment_17 = root_33();
      var node_56 = sibling(first_child(fragment_17), 2);
      {
        var consequent_26 = ($$anchor3) => {
          var div_46 = root_29();
          append($$anchor3, div_46);
        };
        var consequent_28 = ($$anchor3) => {
          var fragment_18 = root_31();
          var div_47 = sibling(first_child(fragment_18), 2);
          var span_12 = child(div_47);
          var text_17 = child(span_12, true);
          reset(span_12);
          var span_13 = sibling(span_12, 4);
          var text_18 = child(span_13);
          reset(span_13);
          var button_23 = sibling(span_13, 6);
          reset(div_47);
          var node_57 = sibling(div_47, 2);
          {
            var consequent_27 = ($$anchor4) => {
              var p = root_30();
              var text_19 = child(p, true);
              reset(p);
              template_effect(() => set_text(text_19, get(analyzeError)));
              append($$anchor4, p);
            };
            if_block(node_57, ($$render) => {
              if (get(analyzeError)) $$render(consequent_27);
            });
          }
          template_effect(() => {
            set_text(text_17, get(analysis).format);
            set_text(text_18, `${get(analysis).node_count ?? ""} nodes`);
          });
          delegated("click", button_23, changeWorkflow);
          append($$anchor3, fragment_18);
        };
        var alternate_2 = ($$anchor3) => {
          var fragment_19 = root_32();
          var div_48 = sibling(first_child(fragment_19), 2);
          let classes_5;
          var textarea = child(div_48);
          remove_textarea_child(textarea);
          set_attribute2(textarea, "placeholder", '{\n  "3": { "class_type": "KSampler", "inputs": { ... } },\n  ...\n}');
          var div_49 = sibling(textarea, 2);
          var button_24 = sibling(child(div_49), 2);
          var input_5 = sibling(button_24, 2);
          bind_this(input_5, ($$value) => set(fileInputEl, $$value), () => get(fileInputEl));
          reset(div_49);
          reset(div_48);
          var node_58 = sibling(div_48, 2);
          {
            var consequent_29 = ($$anchor4) => {
              var p_1 = root_30();
              var text_20 = child(p_1, true);
              reset(p_1);
              template_effect(() => set_text(text_20, get(analyzeError)));
              append($$anchor4, p_1);
            };
            if_block(node_58, ($$render) => {
              if (get(analyzeError)) $$render(consequent_29);
            });
          }
          template_effect(() => classes_5 = set_class(div_48, 1, "dropzone svelte-10v1sym", null, classes_5, { dragover: get(dragOver) }));
          event("drop", div_48, handleDrop);
          event("dragover", div_48, handleDragOver);
          event("dragleave", div_48, handleDragLeave);
          delegated("input", textarea, () => set(analyzeError, ""));
          bind_value(textarea, () => get(rawText), ($$value) => set(rawText, $$value));
          delegated("click", button_24, () => get(fileInputEl)?.click());
          delegated("change", input_5, handleFileInput);
          append($$anchor3, fragment_19);
        };
        if_block(node_56, ($$render) => {
          if (get(editLoading)) $$render(consequent_26);
          else if (get(editPresetId) && get(analysis)) $$render(consequent_28, 1);
          else $$render(alternate_2, -1);
        });
      }
      append($$anchor2, fragment_17);
    };
    var consequent_45 = ($$anchor2) => {
      var fragment_20 = root_52();
      var div_50 = sibling(first_child(fragment_20), 4);
      var span_14 = child(div_50);
      var text_21 = child(span_14, true);
      reset(span_14);
      var span_15 = sibling(span_14, 4);
      var text_22 = child(span_15);
      reset(span_15);
      var node_59 = sibling(span_15, 2);
      {
        var consequent_31 = ($$anchor3) => {
          var fragment_21 = root_34();
          next(2);
          append($$anchor3, fragment_21);
        };
        if_block(node_59, ($$render) => {
          if (get(analysis).object_info_used) $$render(consequent_31);
        });
      }
      var node_60 = sibling(node_59, 2);
      {
        var consequent_32 = ($$anchor3) => {
          var fragment_22 = root_35();
          next(2);
          append($$anchor3, fragment_22);
        };
        if_block(node_60, ($$render) => {
          if (get(analysis).lora_chain) $$render(consequent_32);
        });
      }
      var span_16 = sibling(node_60, 2);
      var node_61 = child(span_16);
      {
        var consequent_33 = ($$anchor3) => {
          var span_17 = root_36();
          append($$anchor3, span_17);
        };
        if_block(node_61, ($$render) => {
          if (typeof window !== "undefined" && window.__potionui?.chat) $$render(consequent_33);
        });
      }
      var button_25 = sibling(node_61, 2);
      reset(span_16);
      reset(div_50);
      var div_51 = sibling(div_50, 2);
      var div_52 = child(div_51);
      var node_62 = sibling(child(div_52), 2);
      {
        var consequent_36 = ($$anchor3) => {
          var div_53 = root_40();
          var node_63 = sibling(child(div_53), 2);
          each(node_63, 17, () => get(loraChainNodes), (n) => n.node_id, ($$anchor4, n) => {
            const isKept = user_derived(() => get(loraConverted) ? get(loraKeptIds).has(get(n).node_id) : get(loraKeepFixed).has(get(n).node_id));
            const isReplaced = user_derived(() => get(loraConverted) && get(loraReplacedIds).has(get(n).node_id));
            var div_54 = root_38();
            var span_18 = child(div_54);
            var text_23 = child(span_18, true);
            reset(span_18);
            var span_19 = sibling(span_18, 2);
            var text_24 = child(span_19, true);
            reset(span_19);
            var span_20 = sibling(span_19, 2);
            var text_25 = child(span_20, true);
            reset(span_20);
            var node_64 = sibling(span_20, 2);
            {
              var consequent_34 = ($$anchor5) => {
                var span_21 = root_37();
                append($$anchor5, span_21);
              };
              if_block(node_64, ($$render) => {
                if (get(isReplaced)) $$render(consequent_34);
              });
            }
            var div_55 = sibling(node_64, 2);
            var button_26 = child(div_55);
            let classes_6;
            var node_65 = child(button_26);
            icon(node_65, () => "lock", () => 13);
            reset(button_26);
            reset(div_55);
            reset(div_54);
            template_effect(() => {
              set_attribute2(div_54, "data-lora-chain-node", get(n).node_id);
              set_text(text_23, get(n).node_id);
              set_text(text_24, get(n).lora_name);
              set_text(text_25, get(n).strength_model);
              classes_6 = set_class(button_26, 1, "iconbtn svelte-10v1sym", null, classes_6, { active: get(isKept) });
              set_attribute2(button_26, "title", get(isKept) ? "Keep fixed (won\u2019t become part of the picker)" : "Keep fixed");
              button_26.disabled = get(loraConverted);
            });
            delegated("click", button_26, () => toggleLoraKeepFixed(get(n).node_id));
            append($$anchor4, div_54);
          });
          var node_66 = sibling(node_63, 2);
          {
            var consequent_35 = ($$anchor4) => {
              var p_2 = root_39();
              var text_26 = child(p_2);
              reset(p_2);
              template_effect(() => set_text(text_26, `Kept LoRA node ${get(loraSandwichError).nodeId ?? ""} sits between replaced nodes ${get(loraSandwichError).beforeId ?? ""} and ${get(loraSandwichError).afterId ?? ""};
										keep all LoRAs above it fixed too, or replace it.`));
              append($$anchor4, p_2);
            };
            if_block(node_66, ($$render) => {
              if (get(loraSandwichError)) $$render(consequent_35);
            });
          }
          var div_56 = sibling(node_66, 2);
          var button_27 = child(div_56);
          var text_27 = child(button_27, true);
          reset(button_27);
          reset(div_56);
          reset(div_53);
          template_effect(() => {
            button_27.disabled = get(loraConverted) || !!get(loraSandwichError);
            set_text(text_27, get(loraConverted) ? "Converted to LoRA picker" : "Convert to LoRA picker");
          });
          delegated("click", button_27, convertLoraChainToPicker);
          append($$anchor3, div_53);
        };
        if_block(node_62, ($$render) => {
          if (get(loraChainNodes).length) $$render(consequent_36);
        });
      }
      var div_57 = sibling(node_62, 2);
      var input_6 = child(div_57);
      remove_input_defaults(input_6);
      reset(div_57);
      var node_67 = sibling(div_57, 2);
      each(node_67, 17, () => get(leftGroups), (g) => g.node_id, ($$anchor3, g) => {
        var fragment_23 = root_46();
        var div_58 = first_child(fragment_23);
        var text_28 = child(div_58);
        var span_22 = sibling(text_28);
        var text_29 = child(span_22, true);
        reset(span_22);
        reset(div_58);
        var node_68 = sibling(div_58, 2);
        each(node_68, 17, () => get(g).rows, (c) => candidateKey(c), ($$anchor4, c) => {
          const key2 = user_derived(() => candidateKey(get(c)));
          const locked = user_derived(() => isLockedCandidate(get(c)));
          const mappedField = user_derived(() => get(mappedFieldByKey).get(get(key2)));
          var div_59 = root_45();
          let classes_7;
          var span_23 = child(div_59);
          var text_30 = child(span_23, true);
          reset(span_23);
          var node_69 = sibling(span_23, 2);
          {
            var consequent_37 = ($$anchor5) => {
              var span_24 = root_41();
              var node_70 = child(span_24);
              icon(node_70, () => "lock", () => 10);
              next();
              reset(span_24);
              append($$anchor5, span_24);
            };
            var consequent_38 = ($$anchor5) => {
              var span_25 = root_42();
              var text_31 = child(span_25, true);
              reset(span_25);
              template_effect(() => set_text(text_31, get(mappedField).label));
              append($$anchor5, span_25);
            };
            var alternate_3 = ($$anchor5) => {
              var fragment_24 = root_43();
              var span_26 = first_child(fragment_24);
              var text_32 = child(span_26, true);
              reset(span_26);
              var span_27 = sibling(span_26, 2);
              var text_33 = child(span_27, true);
              reset(span_27);
              template_effect(
                ($0, $1) => {
                  set_text(text_32, $0);
                  set_text(text_33, $1);
                },
                [
                  () => String(get(c).current_value),
                  () => (get(c).value_type || get(c).suggested_field_type || "").toUpperCase()
                ]
              );
              append($$anchor5, fragment_24);
            };
            if_block(node_69, ($$render) => {
              if (get(locked)) $$render(consequent_37);
              else if (get(mappedField)) $$render(consequent_38, 1);
              else $$render(alternate_3, -1);
            });
          }
          var div_60 = sibling(node_69, 2);
          var node_71 = child(div_60);
          {
            var consequent_39 = ($$anchor5) => {
              icon($$anchor5, () => "check", () => 13);
            };
            var consequent_40 = ($$anchor5) => {
              var button_28 = root_44();
              var node_72 = child(button_28);
              icon(node_72, () => "arrow-right", () => 13);
              reset(button_28);
              delegated("click", button_28, () => addCandidateToForm(get(c)));
              append($$anchor5, button_28);
            };
            if_block(node_71, ($$render) => {
              if (get(mappedField)) $$render(consequent_39);
              else if (!get(locked)) $$render(consequent_40, 1);
            });
          }
          reset(div_60);
          reset(div_59);
          template_effect(() => {
            classes_7 = set_class(div_59, 1, "di-row svelte-10v1sym", null, classes_7, { locked: get(locked), mapped: !!get(mappedField) });
            set_attribute2(div_59, "data-input-key", get(key2));
            set_text(text_30, get(c).input_name);
          });
          append($$anchor4, div_59);
        });
        template_effect(() => {
          set_text(text_28, `${get(g).node_title ?? ""} `);
          set_text(text_29, get(g).class_type);
        });
        append($$anchor3, fragment_23);
      });
      reset(div_52);
      var div_61 = sibling(div_52, 2);
      var div_62 = child(div_61);
      var node_73 = child(div_62);
      each(node_73, 19, () => get(form).tabs, (tab) => tab.id, ($$anchor3, tab, ti) => {
        var span_28 = root_49();
        let classes_8;
        var node_74 = child(span_28);
        {
          var consequent_41 = ($$anchor4) => {
            tabIcon($$anchor4, () => get(tab).icon);
          };
          if_block(node_74, ($$render) => {
            if (get(tab).icon) $$render(consequent_41);
          });
        }
        var node_75 = sibling(node_74, 2);
        {
          var consequent_42 = ($$anchor4) => {
            var input_7 = root_47();
            remove_input_defaults(input_7);
            template_effect(() => set_value(input_7, get(tab).label));
            delegated("click", input_7, (e) => e.stopPropagation());
            event("blur", input_7, (e) => {
              get(tab).label = e.currentTarget.value.trim() || get(tab).label;
              set(renamingTabId, null);
            });
            delegated("keydown", input_7, (e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") set(renamingTabId, null);
            });
            append($$anchor4, input_7);
          };
          var alternate_4 = ($$anchor4) => {
            var text_34 = text();
            template_effect(() => set_text(text_34, get(tab).label));
            append($$anchor4, text_34);
          };
          if_block(node_75, ($$render) => {
            if (get(renamingTabId) === get(tab).id) $$render(consequent_42);
            else $$render(alternate_4, -1);
          });
        }
        var span_29 = sibling(node_75, 2);
        var node_76 = child(span_29);
        icon(node_76, () => "more", () => 11);
        reset(span_29);
        var node_77 = sibling(span_29, 2);
        {
          var consequent_43 = ($$anchor4) => {
            var div_63 = root_48();
            var button_29 = child(div_63);
            var node_78 = child(button_29);
            icon(node_78, () => "pencil");
            next();
            reset(button_29);
            var button_30 = sibling(button_29, 2);
            var node_79 = child(button_30);
            icon(node_79, () => "arrow-left");
            next();
            reset(button_30);
            var button_31 = sibling(button_30, 2);
            var node_80 = child(button_31);
            icon(node_80, () => "arrow-right");
            next();
            reset(button_31);
            var select_2 = sibling(button_31, 6);
            each(select_2, 21, () => TAB_ICON_OPTIONS, index, ($$anchor5, opt) => {
              var option_2 = root_7();
              var text_35 = child(option_2, true);
              reset(option_2);
              var option_2_value = {};
              template_effect(() => {
                set_text(text_35, get(opt).label);
                if (option_2_value !== (option_2_value = get(opt).value)) {
                  option_2.value = (option_2.__value = get(opt).value) ?? "";
                }
              });
              append($$anchor5, option_2);
            });
            reset(select_2);
            var select_2_value;
            init_select(select_2);
            var select_3 = sibling(select_2, 4);
            each(select_3, 21, () => TAB_DISPLAY_OPTIONS, index, ($$anchor5, opt) => {
              var option_3 = root_7();
              var text_36 = child(option_3, true);
              reset(option_3);
              var option_3_value = {};
              template_effect(() => {
                set_text(text_36, get(opt).label);
                if (option_3_value !== (option_3_value = get(opt).value)) {
                  option_3.value = (option_3.__value = get(opt).value) ?? "";
                }
              });
              append($$anchor5, option_3);
            });
            reset(select_3);
            var select_3_value;
            init_select(select_3);
            var button_32 = sibling(select_3, 4);
            var node_81 = child(button_32);
            icon(node_81, () => "x");
            next();
            reset(button_32);
            reset(div_63);
            action(div_63, ($$node, $$action_arg) => floating?.($$node, $$action_arg), () => ({
              anchor: get(tabPopoverAnchorEl),
              onOutsideClick: () => set(tabPopoverId, null)
            }));
            template_effect(() => {
              button_30.disabled = get(ti) === 0;
              button_31.disabled = get(ti) === get(form).tabs.length - 1;
              if (select_2_value !== (select_2_value = get(tab).icon ?? "")) {
                select_2.value = (select_2.__value = get(tab).icon ?? "") ?? "", select_option(select_2, get(tab).icon ?? "");
              }
              select_3.disabled = !get(tab).icon;
              if (select_3_value !== (select_3_value = get(tab).icon_display)) {
                select_3.value = (select_3.__value = get(tab).icon_display) ?? "", select_option(select_3, get(tab).icon_display);
              }
              button_32.disabled = get(form).tabs.length <= 1;
            });
            delegated("click", div_63, (e) => e.stopPropagation());
            delegated("keydown", div_63, (e) => e.stopPropagation());
            delegated("click", button_29, () => {
              set(renamingTabId, get(tab).id, true);
              set(tabPopoverId, null);
            });
            delegated("click", button_30, () => {
              moveTab(get(ti), -1);
              set(tabPopoverId, null);
            });
            delegated("click", button_31, () => {
              moveTab(get(ti), 1);
              set(tabPopoverId, null);
            });
            delegated("change", select_2, (e) => setTabIcon(get(tab), e.currentTarget.value));
            delegated("change", select_3, (e) => setTabDisplay(get(tab), e.currentTarget.value));
            delegated("click", button_32, () => {
              deleteTab(get(ti));
              set(tabPopoverId, null);
            });
            append($$anchor4, div_63);
          };
          if_block(node_77, ($$render) => {
            if (get(tabPopoverId) === get(tab).id) $$render(consequent_43);
          });
        }
        reset(span_28);
        template_effect(() => {
          classes_8 = set_class(span_28, 1, "di-tab svelte-10v1sym", null, classes_8, {
            active: get(tab).id === get(activeTabId),
            "drop-target": get(dragOverTabId) === get(tab).id
          });
          set_attribute2(span_28, "aria-selected", get(tab).id === get(activeTabId));
          set_attribute2(span_28, "data-tab-id", get(tab).id);
        });
        delegated("click", span_28, () => set(activeTabId, get(tab).id, true));
        delegated("keydown", span_28, (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            set(activeTabId, get(tab).id, true);
          }
        });
        event("dragover", span_28, (e) => handleTabDragOver(e, get(tab).id));
        event("dragleave", span_28, () => handleTabDragLeave(get(tab).id));
        event("drop", span_28, (e) => handleTabDrop(e, get(tab).id));
        delegated("click", span_29, (e) => {
          e.stopPropagation();
          toggleTabPopover(get(tab).id, e.currentTarget);
        });
        delegated("keydown", span_29, (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            toggleTabPopover(get(tab).id, e.currentTarget);
          }
        });
        append($$anchor3, span_28);
      });
      var span_30 = sibling(node_73, 2);
      var node_82 = child(span_30);
      icon(node_82, () => "plus", () => 11);
      next();
      reset(span_30);
      reset(div_62);
      var div_64 = sibling(div_62, 2);
      var node_83 = child(div_64);
      {
        var consequent_44 = ($$anchor3) => {
          var div_65 = root_50();
          var node_84 = child(div_65);
          icon(node_84, () => "inbox", () => 24);
          next(2);
          reset(div_65);
          append($$anchor3, div_65);
        };
        if_block(node_83, ($$render) => {
          if (get(activeTab).items.length === 0) $$render(consequent_44);
        });
      }
      var node_85 = sibling(node_83, 2);
      each(node_85, 19, () => get(activeTab).items, (it) => it._id, ($$anchor3, it, index2) => {
        anyItem($$anchor3, () => get(it), () => get(activeTab).items, () => get(index2));
      });
      var node_86 = sibling(node_85, 2);
      addMenu(node_86, () => get(activeTab).items, () => true);
      reset(div_64);
      reset(div_61);
      reset(div_51);
      var div_66 = sibling(div_51, 2);
      var div_67 = child(div_66);
      var input_8 = sibling(child(div_67), 2);
      remove_input_defaults(input_8);
      var datalist = sibling(input_8, 2);
      each(datalist, 21, () => get(families), index, ($$anchor3, f) => {
        var option_4 = root_51();
        var option_4_value = {};
        template_effect(() => {
          if (option_4_value !== (option_4_value = get(f))) {
            option_4.value = (option_4.__value = get(f)) ?? "";
          }
        });
        append($$anchor3, option_4);
      });
      reset(datalist);
      reset(div_67);
      var div_68 = sibling(div_67, 2);
      var input_9 = sibling(child(div_68), 2);
      remove_input_defaults(input_9);
      reset(div_68);
      var div_69 = sibling(div_68, 2);
      var input_10 = sibling(child(div_69), 2);
      remove_input_defaults(input_10);
      reset(div_69);
      reset(div_66);
      template_effect(() => {
        set_text(text_21, get(analysis).format);
        set_text(text_22, `${get(analysis).node_count ?? ""} nodes`);
      });
      delegated("click", button_25, changeWorkflow);
      bind_value(input_6, () => get(leftSearch), ($$value) => set(leftSearch, $$value));
      delegated("click", span_30, addTab);
      delegated("keydown", span_30, (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          addTab();
        }
      });
      bind_value(input_8, () => get(modelFamily), ($$value) => set(modelFamily, $$value));
      bind_value(input_9, () => get(variant), ($$value) => set(variant, $$value));
      bind_value(input_10, () => get(displayName), ($$value) => set(displayName, $$value));
      append($$anchor2, fragment_20);
    };
    var consequent_49 = ($$anchor2) => {
      var fragment_29 = root_59();
      var div_70 = sibling(first_child(fragment_29), 4);
      var div_71 = child(div_70);
      var div_72 = child(div_71);
      var span_31 = sibling(child(div_72));
      var text_37 = child(span_31, true);
      reset(span_31);
      reset(div_72);
      var div_73 = sibling(div_72, 2);
      var div_74 = sibling(child(div_73), 2);
      var input_11 = sibling(child(div_74), 3);
      var span_32 = sibling(input_11);
      var node_87 = child(span_32);
      icon(node_87, () => "lock", () => 10);
      next();
      reset(span_32);
      reset(div_74);
      var div_75 = sibling(div_74, 2);
      var input_12 = sibling(child(div_75), 3);
      var span_33 = sibling(input_12);
      var node_88 = child(span_33);
      icon(node_88, () => "lock", () => 10);
      next();
      reset(span_33);
      reset(div_75);
      var div_76 = sibling(div_75, 2);
      var input_13 = sibling(child(div_76), 3);
      var span_34 = sibling(input_13);
      var node_89 = child(span_34);
      icon(node_89, () => "lock", () => 10);
      next();
      reset(span_34);
      reset(div_76);
      var div_77 = sibling(div_76, 2);
      var input_14 = sibling(child(div_77), 3);
      var span_35 = sibling(input_14);
      var node_90 = child(span_35);
      icon(node_90, () => "lock", () => 10);
      next();
      reset(span_35);
      reset(div_77);
      var node_91 = sibling(div_77, 2);
      each(node_91, 19, () => get(historyRows), (row) => row.field_name, ($$anchor3, row, index2) => {
        const f = user_derived(() => fieldForRow(get(row)));
        var div_78 = root_54();
        let classes_9;
        var div_79 = child(div_78);
        var button_33 = child(div_79);
        var node_92 = child(button_33);
        icon(node_92, () => "chevron-up", () => 10);
        reset(button_33);
        var button_34 = sibling(button_33, 2);
        var node_93 = child(button_34);
        icon(node_93, () => "chevron-down", () => 10);
        reset(button_34);
        reset(div_79);
        var input_15 = sibling(div_79, 2);
        remove_input_defaults(input_15);
        var div_80 = sibling(input_15, 2);
        var span_36 = child(div_80);
        var text_38 = child(span_36, true);
        reset(span_36);
        var span_37 = sibling(span_36, 2);
        var text_39 = child(span_37, true);
        reset(span_37);
        reset(div_80);
        var input_16 = sibling(div_80, 2);
        remove_input_defaults(input_16);
        var select_4 = sibling(input_16, 2);
        each(select_4, 21, () => FORMAT_OPTIONS, index, ($$anchor4, opt) => {
          var option_5 = root_7();
          var text_40 = child(option_5, true);
          reset(option_5);
          var option_5_value = {};
          template_effect(() => {
            set_text(text_40, get(opt).label);
            if (option_5_value !== (option_5_value = get(opt).value)) {
              option_5.value = (option_5.__value = get(opt).value) ?? "";
            }
          });
          append($$anchor4, option_5);
        });
        reset(select_4);
        var select_4_value;
        init_select(select_4);
        var node_94 = sibling(select_4, 2);
        {
          var consequent_46 = ($$anchor4) => {
            var div_81 = root_53();
            var input_17 = sibling(child(div_81), 2);
            remove_input_defaults(input_17);
            var span_38 = sibling(input_17, 2);
            var span_39 = sibling(child(span_38));
            var text_41 = child(span_39, true);
            reset(span_39);
            reset(span_38);
            reset(div_81);
            template_effect(($0) => set_text(text_41, $0), [
              () => get(f) ? formatPreviewValue(get(f), "jinja", get(row).template) : ""
            ]);
            bind_value(input_17, () => get(row).template, ($$value) => get(row).template = $$value);
            append($$anchor4, div_81);
          };
          if_block(node_94, ($$render) => {
            if (get(row).enabled && get(row).format === "jinja") $$render(consequent_46);
          });
        }
        reset(div_78);
        template_effect(
          ($0) => {
            classes_9 = set_class(div_78, 1, "hist-row svelte-10v1sym", null, classes_9, { off: !get(row).enabled });
            set_attribute2(div_78, "data-history-row", get(row).field_name);
            set_text(text_38, get(f)?.label || get(row).field_name);
            set_text(text_39, $0);
            input_16.disabled = !get(row).enabled;
            select_4.disabled = !get(row).enabled;
            if (select_4_value !== (select_4_value = get(row).format)) {
              select_4.value = (select_4.__value = get(row).format) ?? "", select_option(select_4, get(row).format);
            }
          },
          [
            () => get(f)?.mappings?.map((m) => m.input_name).join(" \xB7 ") || get(row).field_name
          ]
        );
        delegated("click", button_33, () => moveHistoryRow(get(index2), -1));
        delegated("click", button_34, () => moveHistoryRow(get(index2), 1));
        bind_checked(input_15, () => get(row).enabled, ($$value) => get(row).enabled = $$value);
        bind_value(input_16, () => get(row).label, ($$value) => get(row).label = $$value);
        delegated("change", select_4, (e) => onFormatChange(get(row), e.currentTarget.value));
        append($$anchor3, div_78);
      });
      reset(div_73);
      reset(div_71);
      var div_82 = sibling(div_71, 2);
      var div_83 = sibling(child(div_82), 2);
      var div_84 = child(div_83);
      var div_85 = child(div_84);
      var node_95 = child(div_85);
      icon(node_95, () => "sliders", () => 14);
      next();
      reset(div_85);
      next(2);
      reset(div_84);
      var div_86 = sibling(div_84, 2);
      var node_96 = sibling(child(div_86), 8);
      each(node_96, 17, () => get(historyRows).filter((r) => r.enabled), (row) => row.field_name, ($$anchor3, row) => {
        const f = user_derived(() => fieldForRow(get(row)));
        var fragment_30 = comment();
        var node_97 = first_child(fragment_30);
        {
          var consequent_47 = ($$anchor4) => {
            var div_87 = root_56();
            var div_88 = child(div_87);
            var text_42 = child(div_88, true);
            reset(div_88);
            var div_89 = sibling(div_88, 2);
            each(div_89, 21, () => get(f).default, index, ($$anchor5, chipItem) => {
              var span_40 = root_55();
              var text_43 = child(span_40, true);
              reset(span_40);
              template_effect(($0) => set_text(text_43, $0), [
                () => typeof get(chipItem) === "object" ? get(chipItem)?.name || JSON.stringify(get(chipItem)) : get(chipItem)
              ]);
              append($$anchor5, span_40);
            });
            reset(div_89);
            reset(div_87);
            template_effect(() => set_text(text_42, get(row).label));
            append($$anchor4, div_87);
          };
          var d_2 = user_derived(() => get(row).format === "list" && get(f) && Array.isArray(get(f).default));
          var alternate_5 = ($$anchor4) => {
            var div_90 = root_57();
            var div_91 = child(div_90);
            var text_44 = child(div_91, true);
            reset(div_91);
            var div_92 = sibling(div_91);
            var text_45 = child(div_92, true);
            reset(div_92);
            reset(div_90);
            template_effect(
              ($0) => {
                set_text(text_44, get(row).label);
                set_text(text_45, $0);
              },
              [
                () => get(f) ? formatPreviewValue(get(f), get(row).format, get(row).template) : ""
              ]
            );
            append($$anchor4, div_90);
          };
          if_block(node_97, ($$render) => {
            if (get(d_2)) $$render(consequent_47);
            else $$render(alternate_5, -1);
          });
        }
        append($$anchor3, fragment_30);
      });
      reset(div_86);
      var node_98 = sibling(div_86, 2);
      {
        var consequent_48 = ($$anchor3) => {
          var div_93 = root_58();
          var node_99 = child(div_93);
          icon(node_99, () => "inbox", () => 20);
          next(2);
          reset(div_93);
          append($$anchor3, div_93);
        };
        var d_3 = user_derived(() => get(historyRows).filter((r) => r.enabled).length === 0);
        if_block(node_98, ($$render) => {
          if (get(d_3)) $$render(consequent_48);
        });
      }
      reset(div_83);
      reset(div_82);
      reset(div_70);
      template_effect(() => set_text(text_37, get(historyRows).length + 4));
      append($$anchor2, fragment_29);
    };
    var consequent_56 = ($$anchor2) => {
      var fragment_31 = root_67();
      var node_100 = sibling(first_child(fragment_31), 6);
      {
        var consequent_50 = ($$anchor3) => {
          var div_94 = root_60();
          append($$anchor3, div_94);
        };
        var consequent_51 = ($$anchor3) => {
          var p_3 = root_61();
          var text_46 = child(p_3, true);
          reset(p_3);
          template_effect(() => set_text(text_46, get(requirementsError)));
          append($$anchor3, p_3);
        };
        var consequent_54 = ($$anchor3) => {
          var fragment_32 = comment();
          var node_101 = first_child(fragment_32);
          {
            var consequent_52 = ($$anchor4) => {
              var div_95 = root_62();
              append($$anchor4, div_95);
            };
            var alternate_6 = ($$anchor4) => {
              var div_96 = root_65();
              each(div_96, 21, () => get(requirementsResults), index, ($$anchor5, r) => {
                var div_97 = root_64();
                var div_98 = child(div_97);
                var div_99 = sibling(div_98, 2);
                var div_100 = child(div_99);
                var text_47 = child(div_100);
                reset(div_100);
                var div_101 = sibling(div_100, 2);
                var text_48 = child(div_101, true);
                reset(div_101);
                var node_102 = sibling(div_101, 2);
                {
                  var consequent_53 = ($$anchor6) => {
                    var div_102 = root_63();
                    var text_49 = child(div_102, true);
                    reset(div_102);
                    template_effect(() => set_text(text_49, get(r).hint));
                    append($$anchor6, div_102);
                  };
                  if_block(node_102, ($$render) => {
                    if (get(r).status !== "ok" && get(r).hint) $$render(consequent_53);
                  });
                }
                reset(div_99);
                reset(div_97);
                template_effect(() => {
                  set_class(div_98, 1, `req-dot ${get(r).status === "ok" ? "ok" : "missing"}`, "svelte-10v1sym");
                  set_text(text_47, `${get(r).type ?? ""}: ${get(r).name ?? ""}`);
                  set_text(text_48, get(r).detail);
                });
                append($$anchor5, div_97);
              });
              reset(div_96);
              append($$anchor4, div_96);
            };
            if_block(node_101, ($$render) => {
              if (get(requirementsResults).length === 0) $$render(consequent_52);
              else $$render(alternate_6, -1);
            });
          }
          append($$anchor3, fragment_32);
        };
        if_block(node_100, ($$render) => {
          if (get(requirementsLoading)) $$render(consequent_50);
          else if (get(requirementsError)) $$render(consequent_51, 1);
          else if (get(requirementsResults)) $$render(consequent_54, 2);
        });
      }
      var node_103 = sibling(node_100, 2);
      {
        var consequent_55 = ($$anchor3) => {
          var p_4 = root_66();
          var text_50 = child(p_4, true);
          reset(p_4);
          template_effect(() => set_text(text_50, get(createError)));
          append($$anchor3, p_4);
        };
        if_block(node_103, ($$render) => {
          if (get(createError)) $$render(consequent_55);
        });
      }
      append($$anchor2, fragment_31);
    };
    var consequent_60 = ($$anchor2) => {
      var fragment_33 = root_72();
      var p_5 = sibling(first_child(fragment_33), 2);
      var text_51 = child(p_5);
      reset(p_5);
      var div_103 = sibling(p_5, 2);
      var p_6 = child(div_103);
      var span_41 = sibling(child(p_6));
      var text_52 = child(span_41, true);
      reset(span_41);
      reset(p_6);
      var node_104 = sibling(p_6, 2);
      {
        var consequent_57 = ($$anchor3) => {
          var div_104 = root_69();
          var ul = sibling(child(div_104), 2);
          each(ul, 21, () => get(createResult).lint.errors, index, ($$anchor4, err) => {
            var li = root_68();
            var text_53 = child(li, true);
            reset(li);
            template_effect(() => set_text(text_53, get(err)));
            append($$anchor4, li);
          });
          reset(ul);
          reset(div_104);
          append($$anchor3, div_104);
        };
        if_block(node_104, ($$render) => {
          if (get(createResult).lint.errors.length > 0) $$render(consequent_57);
        });
      }
      var node_105 = sibling(node_104, 2);
      {
        var consequent_58 = ($$anchor3) => {
          var fragment_34 = comment();
          var node_106 = first_child(fragment_34);
          each(node_106, 17, () => get(createResult).lint.warnings, index, ($$anchor4, warn) => {
            var div_105 = root_70();
            var div_106 = sibling(child(div_105), 2);
            var text_54 = child(div_106, true);
            reset(div_106);
            reset(div_105);
            template_effect(() => set_text(text_54, get(warn)));
            append($$anchor4, div_105);
          });
          append($$anchor3, fragment_34);
        };
        if_block(node_105, ($$render) => {
          if (get(createResult).lint.warnings.length > 0) $$render(consequent_58);
        });
      }
      var node_107 = sibling(node_105, 2);
      {
        var consequent_59 = ($$anchor3) => {
          var p_7 = root_71();
          append($$anchor3, p_7);
        };
        if_block(node_107, ($$render) => {
          if (get(createResult).lint.errors.length === 0 && get(createResult).lint.warnings.length === 0) $$render(consequent_59);
        });
      }
      reset(div_103);
      template_effect(() => {
        set_text(text_51, `It's ready in Presets${get(requirementsMissingCount) > 0 ? ` \u2014 the ${get(requirementsMissingCount)} missing requirement${get(requirementsMissingCount) === 1 ? "" : "s"} from the last step won't block it from opening, only from running.` : "."}`);
        set_text(text_52, get(createResult).path);
      });
      append($$anchor2, fragment_33);
    };
    if_block(node_55, ($$render) => {
      if (get(step) === 1) $$render(consequent_30);
      else if (get(step) === 2) $$render(consequent_45, 1);
      else if (get(step) === 3) $$render(consequent_49, 2);
      else if (get(step) === 4) $$render(consequent_56, 3);
      else if (get(step) === 5 && get(createResult)) $$render(consequent_60, 4);
    });
  }
  reset(div_44);
  var div_107 = sibling(div_44, 2);
  var node_108 = child(div_107);
  {
    var consequent_61 = ($$anchor2) => {
      var fragment_35 = root_73();
      var button_35 = first_child(fragment_35);
      var a_1 = sibling(button_35, 4);
      template_effect(() => set_attribute2(a_1, "href", `/admin?tab=presets&preset=${get(createResult)?.preset_id ?? ""}`));
      delegated("click", button_35, importAnother);
      append($$anchor2, fragment_35);
    };
    var alternate_7 = ($$anchor2) => {
      var fragment_36 = root_79();
      var node_109 = first_child(fragment_36);
      {
        var consequent_62 = ($$anchor3) => {
          var button_36 = root_74();
          template_effect(() => button_36.disabled = get(analyzing) || get(creating));
          delegated("click", button_36, goBack);
          append($$anchor3, button_36);
        };
        if_block(node_109, ($$render) => {
          if (get(step) > 1) $$render(consequent_62);
        });
      }
      var node_110 = sibling(node_109, 4);
      {
        var consequent_63 = ($$anchor3) => {
          var button_37 = root_75();
          var text_55 = child(button_37, true);
          reset(button_37);
          template_effect(
            ($0) => {
              button_37.disabled = $0;
              set_text(text_55, get(analyzing) ? "Analyzing\u2026" : "Continue");
            },
            [
              () => get(analyzing) || get(editLoading) || !(get(analysis) && get(workflowJson)) && !get(rawText).trim()
            ]
          );
          delegated("click", button_37, handleSourceContinue);
          append($$anchor3, button_37);
        };
        var consequent_64 = ($$anchor3) => {
          var button_38 = root_76();
          template_effect(() => button_38.disabled = !get(canContinueForm));
          delegated("click", button_38, goToHistory);
          append($$anchor3, button_38);
        };
        var consequent_65 = ($$anchor3) => {
          var button_39 = root_77();
          delegated("click", button_39, goToRequirementsStep);
          append($$anchor3, button_39);
        };
        var consequent_66 = ($$anchor3) => {
          var button_40 = root_78();
          var text_56 = child(button_40, true);
          reset(button_40);
          template_effect(() => {
            button_40.disabled = get(creating);
            set_text(text_56, get(creating) ? get(editPresetId) ? "Updating\u2026" : "Creating\u2026" : get(editPresetId) ? "Update preset" : "Continue");
          });
          delegated("click", button_40, runCreate);
          append($$anchor3, button_40);
        };
        if_block(node_110, ($$render) => {
          if (get(step) === 1) $$render(consequent_63);
          else if (get(step) === 2) $$render(consequent_64, 1);
          else if (get(step) === 3) $$render(consequent_65, 2);
          else if (get(step) === 4) $$render(consequent_66, 3);
        });
      }
      append($$anchor2, fragment_36);
    };
    if_block(node_108, ($$render) => {
      if (get(step) === 5) $$render(consequent_61);
      else $$render(alternate_7, -1);
    });
  }
  reset(div_107);
  reset(div_43);
  reset(div_22);
  template_effect(
    ($0, $1, $2, $3, $4) => {
      set_class(div_24, 1, `wiz-step ${$0 ?? ""}`, "svelte-10v1sym");
      set_class(div_28, 1, `wiz-step ${$1 ?? ""}`, "svelte-10v1sym");
      set_text(text_13, get(formFieldCount) > 0 ? `${get(formFieldCount)} field${get(formFieldCount) === 1 ? "" : "s"} designed` : "design the form");
      set_class(div_32, 1, `wiz-step ${$2 ?? ""}`, "svelte-10v1sym");
      set_class(div_36, 1, `wiz-step ${$3 ?? ""}`, "svelte-10v1sym");
      set_class(div_41, 1, `wiz-step ${$4 ?? ""}`, "svelte-10v1sym");
    },
    [
      () => stepState(1),
      () => stepState(2),
      () => stepState(3),
      () => stepState(4),
      () => stepState(5)
    ]
  );
  append($$anchor, fragment_16);
  return pop($$exports);
}
delegate(["click", "keydown", "change", "input"]);
export {
  ImportWorkflowTab as default
};
//# sourceMappingURL=ImportWorkflowTab.js.map
