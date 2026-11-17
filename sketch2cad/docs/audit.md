# Auditoría: cadenas de eventos

Toda acción relevante deja una fila en `event` (spec §14.1). Las filas son inmutables (trigger `event_immutable`, migración `0002_sources_events`) y se encadenan con

```
hash = SHA-256(prev_hash || JCS(evento sin hash))
```

donde `prev_hash` es el hash hexadecimal en minúsculas del evento anterior de la misma cadena, codificado en ASCII, y JCS es RFC 8785 (`sketch2cad.audit.canonical`). El verificador `sketch2cad.audit.verify.verify_chain` solo depende de la biblioteca estándar y de `rfc8785`.

## Dos tipos de cadena

| Cadena | `case_file_id` | Génesis (`prev_hash` del primer evento) | Serialización de escrituras |
|---|---|---|---|
| Una por expediente | UUID del expediente | `SHA-256(str(case_file_id))` | Bloqueo de fila `case_file ... FOR UPDATE` |
| Global de autenticación | `NULL` (JSON `null` en el contenido hasheado) | `SHA-256(b"auth")` | `pg_advisory_xact_lock(AUTH_CHAIN_LOCK_KEY)` |

En los dos casos el `id` se toma de la secuencia mientras se tiene el bloqueo, de modo que los `id` crecen a lo largo de cada cadena, y la escritura ocurre en la transacción del cambio (aislamiento `READ COMMITTED`).

## Decisión: cadena global para `auth.login` y `auth.failed` (tarea 001-033)

Los inicios de sesión no pertenecen a ningún expediente. Se eligió una **cadena global** en la misma tabla `event`, en lugar de una tabla `auth_event` separada:

- Reutiliza el escritor (`append_event(case_file_id=None, ...)`), el formato canónico, el trigger de inmutabilidad y el verificador sin duplicar código.
- La migración `0003_global_auth_chain` hace `event.case_file_id` anulable y añade el índice parcial `ix_event_global_chain_id ON event (id) WHERE case_file_id IS NULL`, que sirve la lectura del último hash (`WHERE case_file_id IS NULL ORDER BY id DESC LIMIT 1`). El índice único `(case_file_id, id)` sigue cubriendo las cadenas por expediente; en la cadena global la unicidad la da la clave primaria `id`.
- `verify_chain` reconoce la cadena global porque su primer evento tiene `case_file_id` nulo; entonces la ancla en `SHA-256(b"auth")` y exige que todos los eventos tengan `case_file_id` nulo. Un evento de expediente dentro de la cadena global, o al revés, se rechaza con `case_file_mismatch`.
- El downgrade de `0003` se niega a ejecutarse si existen eventos globales: son de solo inserción y no se pueden borrar para restaurar `NOT NULL`.
- Coste: todos los inicios de sesión del sistema se serializan sobre un único bloqueo consultivo. Para el volumen del MVP es aceptable; si dejara de serlo, la cadena podría particionarse (por ejemplo, por día) con un génesis propio por partición.

## Contenido de los eventos de autenticación

Ningún evento contiene la contraseña enviada, el token de sesión, su SHA-256, el token CSRF ni ningún valor de cookie.

| `kind` | `actor_id` / `actor_role` | `after` |
|---|---|---|
| `auth.login` | usuario autenticado | `{"session_id": "<uuid de la fila session>"}` |
| `auth.failed`, usuario inexistente | nulos | `{"cause": "unknown_user", "username_sha256": "<SHA-256 del nombre de usuario en minúsculas>"}` |
| `auth.failed`, usuario inactivo | usuario | `{"cause": "inactive_user"}` |
| `auth.failed`, cuenta bloqueada | usuario | `{"cause": "account_locked", "locked_until": "<ISO 8601 UTC>"}` |
| `auth.failed`, contraseña incorrecta | usuario | `{"cause": "invalid_credentials", "failed_attempts": n}` y `locked_until` si ese intento bloquea la cuenta |

`client_ip` guarda la dirección del cliente y `occurred_at` el reloj de la petición. La especificación (§14, §15) no autoriza a guardar el identificador tecleado cuando no corresponde a ningún usuario, por eso solo se guarda su SHA-256 en minúsculas: permite correlacionar intentos repetidos contra un mismo nombre sin almacenarlo. La respuesta HTTP no cambia: un usuario inexistente recibe el mismo 401 que una contraseña incorrecta.

Un intento fallido confirma (`COMMIT`) su evento, junto con el contador de bloqueo cuando aplica, antes de devolver el error, de modo que el evento sobrevive a la petición fallida. `auth.login` se confirma en la misma transacción que crea la sesión.
