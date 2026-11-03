float fadeIn(float index, float count) {
    return min(65535.0, max(0.0, index) * floor(65536.0 / count));
}

vec3 scaleBy(vec3 v, float k) {
    return k < 65535.0 ? floor((v * k + 32768.0) / 65536.0) : v;
}

vec4 effect(vec4 color, vec2 uv) {
    float index = float(u_frame);
    vec3 v = floor(color.rgb * 255.0 + 0.5);
    if (u_in_frames > 0.0) v = scaleBy(v, fadeIn(index, u_in_frames));
    if (u_out_frames > 0.0 && index >= u_out_frame) v = scaleBy(v, 65535.0 - fadeIn(index - u_out_frame, u_out_frames));
    return vec4(v / 255.0, color.a);
}
