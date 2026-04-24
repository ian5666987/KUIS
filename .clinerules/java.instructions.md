---
paths:
  - "**/*.java"    # Applyable to all Java files
---

# Règles Clean Code — Java (référence **Java 21**)

Ce document définit un socle de règles **Clean Code** pour développer en **Java 21**. Il vise la lisibilité, la maintenabilité, la testabilité et la robustesse.

## Objectifs

- Code facile à lire en premier (l’ordinateur exécutera de toute façon).
- Changements sûrs (tests, invariants, contrats).
- APIs explicites, comportements prévisibles.
- Dette technique minimale : pas de “quick fixes” non documentés.

---

## 1) Style général et lisibilité

### 1.1 Formatage et conventions

- Utiliser un formatage cohérent sur tout le projet (via IDE/formatter).
- Indentation constante (souvent 4 espaces en Java).
- Une instruction par ligne.
- Limiter la longueur des lignes (ex. 120). Si une ligne dépasse, la découper proprement.
- Préférer les accolades toujours présentes, même pour les blocs d’une ligne :

```java
if (condition) {
    doSomething();
}
```

### 1.2 Nommage

- Noms **intention-révélateurs** : un lecteur doit comprendre le “pourquoi”.
- Éviter les abréviations opaques (sauf standards : id, url, dto, json, xml).
- Variables : noms concrets (`customerId`, `expiresAt`).
- Méthodes : verbes + complément (`calculateTotal`, `findById`).
- Booléens : préfixes `is/has/can/should`.
- Classes : noms de concepts métier (ubiquitous language).

### 1.3 Commentaires

- Le code doit se commenter lui-même (bons noms, bonne structure).
- Commentaires autorisés quand ils apportent :
  - un **pourquoi** (rationale),
  - une contrainte externe (bug connu, spec, RFC),
  - un avertissement (sécurité, perf, compat).
- Pas de commentaires redondants (“getter de x”).
- Pas de code commenté (supprimer, et s’appuyer sur Git).

---

## 2) Fonctions / méthodes

### 2.1 Taille et responsabilité

- Une méthode fait **une seule chose** (Single Responsibility).
- Méthodes courtes : viser la clarté plutôt qu’une limite stricte.
- Extraire des méthodes privées pour nommer les étapes.

### 2.2 Paramètres

- Limiter le nombre de paramètres (0–3 idéal).
- Quand plusieurs paramètres forment un concept, créer un type dédié (record / class).
- Éviter les booléens “drapeaux” (`doX(true)`). Préférer deux méthodes explicites ou un enum.

### 2.3 Valeurs de retour et erreurs

- Préférer retourner des objets cohérents plutôt que `null`.
- Utiliser `Optional<T>` **uniquement** pour les retours (pas pour les champs/paramètres en général).
- Ne pas utiliser `Optional.get()` sans contrôle ; préférer `orElseThrow`, `orElse`, `map`.

---

## 3) Null-safety et API explicites

- Interdire les retours `null` sur les APIs publiques sauf cas exceptionnel documenté.
- Valider les entrées au plus près de la frontière (API, contrôleur, adaptateur).
- Utiliser `Objects.requireNonNull` pour les invariants internes.
- Utiliser des exceptions adaptées (voir §5) plutôt que `null` silencieux.

Exemple :

```java
public Customer getCustomer(CustomerId id) {
    Objects.requireNonNull(id, "id");
    return repository.findById(id)
        .orElseThrow(() -> new CustomerNotFoundException(id));
}
```

---

## 4) Conception orientée objet (SOLID) et modularité

### 4.1 SRP, OCP, DIP

- **SRP** : une classe a une raison unique de changer.
- **OCP** : étendre via polymorphisme/composition plutôt que `if/else` infinis.
- **DIP** : dépendre d’abstractions (interfaces) aux frontières (I/O, DB, HTTP).

### 4.2 Composition plutôt qu’héritage

- Préférer la composition.
- Héritage si la relation “est-un” est stable et justifiée.

### 4.3 Immutabilité

- Favoriser les objets immuables : moins de bugs, plus simple à raisonner.
- Utiliser **records (Java 21)** pour les DTO/valeurs immuables.
- Exposer des collections non modifiables (`List.copyOf`, `Map.copyOf`).

---

## 5) Exceptions, gestion d’erreurs et contrats

- Ne pas attraper `Exception`/`Throwable` sauf au tout dernier niveau (boundary) pour logging/translation.
- Ne pas ignorer une exception (pas de `catch (e) {}` vide).
- Une exception doit être :
  - spécifique,
  - utile (message + contexte),
  - documentée si elle fait partie du contrat.
- Préférer des exceptions runtime spécifiques pour les erreurs métier/validation.
- Pour les erreurs récupérables : préférer un résultat explicite (ex. `Either` via lib) ou un type de retour dédié.

---

## 6) Collections, streams et performance lisible

- Préférer la solution la plus lisible.
- Streams :
  - OK pour transformations/filtrages,
  - éviter les effets de bord,
  - éviter les pipelines complexes : extraire des fonctions nommées.
- `parallelStream()` uniquement avec preuve de gain et absence de contention.
- Utiliser `var` localement quand le type est évident, sinon garder le type explicite.

Exemple :

```java
var activeCustomers = customers.stream()
    .filter(Customer::isActive)
    .toList();
```

---

## 7) Java 21 : fonctionnalités à utiliser proprement

### 7.1 Records

- Utiliser `record` pour les valeurs immuables et DTO.
- Valider dans le compact constructor si nécessaire.

```java
public record Email(String value) {
    public Email {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("email must not be blank");
        }
    }
}
```

### 7.2 Switch expressions et pattern matching (quand activé / disponible)

- Préférer `switch` expression pour éviter duplication et fallthrough.
- Utiliser le pattern matching quand il **simplifie** réellement la logique.

```java
String label = switch (status) {
    case NEW -> "New";
    case DONE -> "Done";
    case FAILED -> "Failed";
};
```

### 7.3 Sealed classes (si pertinent)

- Utiliser `sealed` pour modéliser des hiérarchies fermées (domain states).
- Garantit l’exhaustivité (utile avec `switch`).

### 7.4 Virtual threads (Project Loom)

- Utiliser les **threads virtuels** pour des traitements I/O bloquants quand l’architecture s’y prête.
- Ne pas mélanger sans raison plusieurs modèles de concurrence.
- Mesurer et tester (observabilité, timeouts, saturation).

---

## 8) Tests (qualité et non-régression)

- Écrire des tests unitaires sur la logique métier.
- Utiliser l’injection de dépendances pour isoler I/O.
- Noms de tests descriptifs : `shouldReturnX_whenY()`.
- AAA (Arrange-Act-Assert) ou Given/When/Then.
- Tests rapides, déterministes, indépendants.
- Couvrir : cas nominal, bords, erreurs.

---

## 9) Logging et observabilité

- Logger au bon niveau (DEBUG/INFO/WARN/ERROR).
- Ne pas logger de secrets (tokens, mots de passe, données sensibles).
- Messages structurés si possible (clé/valeur), inclure un identifiant de corrélation.
- Ne pas “logger et relancer” partout : éviter la duplication. Logger aux frontières.

---

## 10) Concurrence et sûreté

- Minimiser l’état mutable partagé.
- Favoriser l’immutabilité, confinement, et synchronisation explicite si nécessaire.
- Documenter les invariants de thread-safety.
- Toujours utiliser timeouts sur I/O (HTTP/DB) et prévoir la cancellation.

---

## 11) Packaging et structure du projet

- Packages par **feature/domaine** plutôt que par couche technique pure.
- Séparer clairement :
  - domaine (pur),
  - application/use-cases,
  - infrastructure/adapters.
- Interfaces côté domaine/application, implémentations côté infrastructure.

---

## 12) Revue rapide (checklist)

- [ ] Les noms expliquent l’intention (variables, méthodes, classes).
- [ ] Pas de duplication inutile.
- [ ] Méthodes courtes et cohérentes (une responsabilité).
- [ ] Entrées validées, pas de `null` surprise.
- [ ] Exceptions spécifiques et utiles.
- [ ] Tests présents sur la logique critique.
- [ ] Pas de secrets dans les logs.
- [ ] Utilisation pertinente des features Java 21 (records, switch expressions, virtual threads si besoin).


