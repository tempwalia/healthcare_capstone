function compile(pattern) {
  const paramNames = [];
  const regexStr =
    "^" +
    pattern.replace(/:[a-zA-Z0-9_]+/g, (m) => {
      paramNames.push(m.slice(1));
      return "([^/]+)";
    }) +
    "$";
  return { regex: new RegExp(regexStr), paramNames };
}

function currentPath() {
  const raw = location.hash.slice(1).split("?")[0];
  const trimmed = raw.replace(/^\/+|\/+$/g, "");
  return "/" + trimmed;
}

export function createRouter() {
  const routes = [];
  let cleanup = null;
  let notFoundHandler = null;

  function add(pattern, handler) {
    const { regex, paramNames } = compile(pattern);
    routes.push({ regex, paramNames, handler });
  }

  function notFound(handler) {
    notFoundHandler = handler;
  }

  async function resolve() {
    if (cleanup) {
      try {
        cleanup();
      } catch (err) {
        console.error(err);
      }
      cleanup = null;
    }
    const path = currentPath();
    for (const route of routes) {
      const m = path.match(route.regex);
      if (m) {
        const params = {};
        route.paramNames.forEach((name, i) => (params[name] = decodeURIComponent(m[i + 1])));
        const result = await route.handler(params);
        if (typeof result === "function") cleanup = result;
        return;
      }
    }
    if (notFoundHandler) notFoundHandler();
  }

  function start() {
    window.addEventListener("hashchange", resolve);
    resolve();
  }

  return { add, notFound, start, resolve };
}

export function navigate(hash) {
  const target = hash.startsWith("#") ? hash : `#${hash}`;
  if (location.hash === target) {
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else {
    location.hash = target;
  }
}
