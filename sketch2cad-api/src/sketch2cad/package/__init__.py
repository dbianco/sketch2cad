"""Final package: manifest, Ed25519 signature and signing key handling (spec section 14).

Deliberately empty: :mod:`sketch2cad.package.manifest` and :mod:`sketch2cad.package.signing`
depend only on the standard library, ``rfc8785`` and ``cryptography``, and importing this
package must not pull in the rest of the application.
"""
