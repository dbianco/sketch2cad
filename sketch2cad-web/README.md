# sketch2cad-web

Editor web de Sketch2CAD (React 18, TypeScript, Vite). Todos los comandos se ejecutan desde esta carpeta.

## Instalar dependencias

```sh
pnpm install --frozen-lockfile
```

## Desarrollo

```sh
pnpm dev
```

El servidor de desarrollo redirige `/api` a la API local en `http://localhost:8000` (ver `vite.config.ts`).

## Tests

```sh
pnpm test --run
```

## Lint y tipos

```sh
pnpm lint
pnpm exec tsc --noEmit
```

## Build

```sh
pnpm build
```

El resultado queda en `dist/`.
