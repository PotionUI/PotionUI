vec3 texel(ivec2 at) {
    ivec2 last = ivec2(u_resolution) - 1;
    return floor(texelFetch(u_input, clamp(at, ivec2(0), last), 0).rgb * 255.0 + 0.5);
}

vec4 effect(vec4 color, vec2 uv) {
    float p = clamp(u_time * u_fps / u_span, 0.0, 1.0);
    float e = u_smooth > 0.5 ? p * p * (3.0 - 2.0 * p) : p;
    float z = u_zoom_from + (u_zoom_to - u_zoom_from) * e;
    float hw = 0.5 / z;
    vec2 anchor = vec2(u_fx + (u_tx - u_fx) * e, u_fy + (u_ty - u_fy) * e);
    vec2 corner = (anchor * (1.0 - 2.0 * hw)) * u_resolution;
    ivec2 fixedPoint = ivec2(floor((corner + floor(uv * u_resolution) / z) * 256.0 + 0.5));
    ivec2 base = fixedPoint >> 8;
    vec2 sub = vec2(fixedPoint & 255);
    vec3 a = texel(base);
    vec3 b = texel(base + ivec2(1, 0));
    vec3 c = texel(base + ivec2(0, 1));
    vec3 d = texel(base + ivec2(1, 1));
    vec3 sum = (256.0 - sub.x) * ((256.0 - sub.y) * a + sub.y * c) + sub.x * ((256.0 - sub.y) * b + sub.y * d);
    return vec4(floor((sum + 32768.0) / 65536.0) / 255.0, color.a);
}
