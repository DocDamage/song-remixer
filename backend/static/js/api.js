export function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export async function getErrorMessage(response, fallbackMessage) {
    const responseText = await response.text();
    if (!responseText) return fallbackMessage;
    try {
        const payload = JSON.parse(responseText);
        if (typeof payload.detail === 'string') return payload.detail;
        if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg || JSON.stringify(item)).join(', ');
        if (typeof payload.message === 'string') return payload.message;
    } catch (_error) {
        return responseText;
    }
    return fallbackMessage;
}

export async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(await getErrorMessage(response, 'Request failed'));
    return response.json();
}

export async function postForm(url, formData) {
    const response = await fetch(url, { method: 'POST', body: formData });
    if (!response.ok) throw new Error(await getErrorMessage(response, 'Request failed'));
    return response;
}
