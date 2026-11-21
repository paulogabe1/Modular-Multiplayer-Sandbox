# WishCraft — Build Journal & Tutorial

**Zero to hero, on the record.** Every decision, every tradeoff, every bug, and the specific
files and calls behind them — written so that after reading it you could explain this system to a
beginner or defend it in an interview.

---

## How to read this

**Evidence classes.** Not every statement here has the same backing, and pretending otherwise
would be the fastest way to make this document useless. Each claim is labelled:

| Mark | Meaning |
|---|---|
| **(Source)** | Read directly from a file in this project during a session. The most reliable. |
| **(Measured)** | A log line or a property read from a live PIE session, quoted. |
| **(Inferred)** | Reasoning from engine source or engine rules, not measured. Usually correct, always arguable. |

Where an early measurement was lost (the log rotates), the section says so rather than quoting a
number it cannot back.

**Conventions.** `Class::Function` names a specific call. `file:line` is a real location in this
project at the time of writing. `UFUNCTION(Server, Reliable)` and friends appear verbatim so you
can match them to the source.

**The one-sentence version.** Plain text becomes a validated list of ability tokens, that list
replicates as *data*, and every machine derives its own ability *behaviour* from it locally — so
the network only ever carries small structs and a few booleans, never components.

**Two documents.** This one is the game: networking, replication, abilities, the test rig. The
intent model — the small neural network meant to replace the keyword parser — has its own
tutorial in **`Docs/IntentModelTutorial.md`**, because it shares almost nothing with the rest of
the build: different language, different toolchain, and four measurement traps that deserve their
own space. This document keeps the model's *evidence* in PART 11 and links out for the teaching,
so neither half has to be read to understand the other.

---

# PART 0 — Orientation

## 0.1 What this actually is

A one-week portfolio prototype of a multiplayer sandbox: the host types a rule in plain English
("fireballs and invisibility only"), it becomes a closed-vocabulary list of ability tokens, gets
validated and clamped, and the live match changes for everyone.

Three abilities: **Fireball** (Offense, a projectile), **Invisibility** (Defense, hides the
avatar), **Speed Boost** (Mobility, ×1.6 movement for 5 s).

The interesting engineering is not the abilities. It is that a *data-driven, server-authoritative,
replicated* ability system was built without GAS, in C++, in a week — and the failures along the
way teach more about UE networking than the success does.

## 0.2 The vocabulary, in plain English

You cannot debug what you cannot name, so:

| Word | What it means here |
|---|---|
| **Authority** | The machine that is allowed to decide. For this game it is the server. `HasAuthority()` is the guard you will see everywhere. |
| **Server / host** | The player who is also running the game. There is no dedicated server in this project. |
| **Client** | Any other player. In testing there is exactly one. |
| **Replicate** | Continuously copy *state* from server to clients. Different from an RPC. |
| **RPC** | A *one-shot function call* sent across the network. Has direction and a reliability flag. |
| **Relevancy** | Whether an actor is even worth sending to a particular connection. This project's biggest bug lived here. |
| **Owner** | The actor that "owns" another for networking purposes. `PlayerController` owns its `Pawn`. |
| **Prediction / correction** | Client guessing ahead and being fixed up. *Not used in this project* — documented as a deliberate tradeoff in D4. |

---

# PART 1 — Project setup from zero

## 1.1 `WishCraft.uproject` — the project descriptor

```json
{
  "FileVersion": 3,
  "EngineAssociation": "5.8",
  "Modules": [ { "Name": "WishCraft", "Type": "Runtime", "LoadingPhase": "Default",
                 "AdditionalDependencies": ["Engine", "AIModule", "UMG"] } ],
  "Plugins": [ ... ModelContextProtocol, Aura, StateTree, GameplayStateTree, ModelingToolsEditorMode ... ]
}
```
*(Source)*

**What each part does:**

- `EngineAssociation: "5.8"` — which installed engine this project binds to. It is how the editor
  picks `C:\Program Files\Epic Games\UE_5.8` without you typing a path.
- `Modules` — the C++ compile units. One `Runtime` module named `WishCraft`, loaded at `Default`
  phase (i.e. when the engine boots, not later). This is what makes the classes available to
  Blueprints and to the game.
- `AdditionalDependencies` — a hint list for tooling. It is **not** what actually controls linking;
  `Build.cs` does (next section). A common beginner trap is editing this list and expecting the
  build to change.
- `Plugins` — `ModelContextProtocol` and `Aura` are what let an assistant drive this editor.
  `StateTree`/`GameplayStateTree` come along with the 5.8 third-person template. None of these are
  required by the gameplay systems described here.

**Decision D0 — do not add a second module.** A single flat `WishCraft` module with no
`Public/Private` split. Tradeoff: everything is reachable from everything (fast to build, no
header-export discipline), and in exchange compile times grow with the codebase and internal
details are not actually private. Correct for a one-week prototype; wrong at 10× the size.

## 1.2 The two `Target.cs` files

```csharp
// WishCraft.Target.cs
public class WishCraftTarget : TargetRules
{
    public WishCraftTarget(TargetInfo Target) : base(Target)
    {
        Type = TargetType.Game;
        DefaultBuildSettings = BuildSettingsVersion.V7;
        IncludeOrderVersion = EngineIncludeOrderVersion.Unreal5_8;
        ExtraModuleNames.Add("WishCraft");
    }
}
```
*(Source — `WishCraftEditor.Target.cs` is identical except `Type = TargetType.Editor`.)*

**Why two files.** A **Target** is a *thing you can build*, and there are two things: the packaged
game (`Game`) and the editor (`Editor`). The editor target is the one that produces
`UnrealEditor-WishCraft.dll`, which is what the editor loads when you press Play. This is why
"build the editor target" is the phrase that matters during development.

**`IncludeOrderVersion`** deserves a note because it is a real 5.x footgun: it pins which ordering
rules apply to the engine's own headers. Changing it changes whether some code compiles at all.
`Unreal5_8` here matches the engine.

## 1.3 `Build.cs` — the file that actually controls linking

```csharp
public class WishCraft : ModuleRules
{
    public WishCraft(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[] {
            "Core", "CoreUObject", "Engine", "InputCore",
            "EnhancedInput", "AIModule",
            "StateTreeModule", "GameplayStateTreeModule",
            "UMG", "Slate"
        });

        PrivateDependencyModuleNames.AddRange(new string[] { });

        PublicIncludePaths.AddRange(new string[] {
            "WishCraft",
            "WishCraft/Abilities",
            "WishCraft/Rules",
            "WishCraft/Variant_Platforming", ... "WishCraft/Variant_SideScrolling/UI"
        });
    }
}
```
*(Source)*

**Line by line, because this is the file people skip:**

- **`PCHUsage`** — precompiled headers. `UseExplicitOrSharedPCHs` means the module may use the
  engine's shared PCH but must declare `#include "CoreMinimal.h"` itself. If you delete that
  include you get a pile of unrelated-looking errors.
- **`PublicDependencyModuleNames` vs `PrivateDependencyModuleNames`** — public dependencies are
  also visible to anything that depends on *you*; private ones are only for your own `.cpp` files.
  With one module the distinction is academic, so everything is public.
- **`Engine`** — and this is the key point for a networking tutorial: **`DOREPLIFETIME`,
  `UPROPERTY(Replicated)`, `UFUNCTION(Server/Client/NetMulticast)` and `GetLifetimeReplicatedProps`
  all live in `Engine`** (`Net/UnrealNetwork.h` ships with it). There is **no** separate
  `"NetCore"` or `"Replication"` module to add. Adding one does nothing; forgetting `Engine` breaks
  everything.
- **`EnhancedInput`** — needed for `UEnhancedInputComponent`, `UInputAction`,
  `UEnhancedInputLocalPlayerSubsystem`. Without it, `InputActionValue.h` does not resolve.
- **`UMG` / `Slate`** — `UUserWidget`, `CreateWidget`, `AddToPlayerScreen`, and
  `FInputModeGameAndUI` for the rule panel.
- **`AIModule`, `StateTreeModule`, `GameplayStateTreeModule`** — 5.8 third-person template
  carry-overs. Nothing in the ability system uses them.
- **`PublicIncludePaths`** — why the code writes `#include "Abilities/AbilityTypes.h"` instead of
  `#include "../../Abilities/AbilityTypes.h"`. The `WishCraft/Abilities` entry makes
  `Abilities/...` resolve. **Wart:** the `Variant_*` entries are leftovers from the template's
  variant levels and point at folders this project does not meaningfully use. Harmless, but they
  would be deleted in a cleanup pass.

**The build-failure lesson that belongs here.** Three `.cpp` files
(`AbilityProjectile.cpp`, `HealthComponent.cpp`, `WishCraftGameMode.cpp`) failed to compile with
"`LogSandbox` is undeclared" because `LogSandbox` is *declared* in `AbilityTypes.h` and *defined*
in `AbilityComponentBase.cpp`. A header that is not included is not visible — each translation unit
must include `Abilities/AbilityTypes.h` itself. There is no magic here; there is only includes.

## 1.4 Config: `DefaultEngine.ini`

The lines that matter for gameplay:

```ini
[/Script/EngineSettings.GameMapsSettings]
GameDefaultMap=/Game/Maps/Lvl_Arena.Lvl_Arena
EditorStartupMap=/Game/ThirdPerson/Lvl_ThirdPerson.Lvl_ThirdPerson
GlobalDefaultGameMode=/Game/ThirdPerson/Blueprints/BP_ThirdPersonGameMode.BP_ThirdPersonGameMode_C
```
*(Source)*

**Read it carefully, because it explains a recurring annoyance:** the *game* starts on `Lvl_Arena`,
but the *editor* opens `Lvl_ThirdPerson`. So every fresh editor session lands on the wrong map for
multiplayer testing and has to be told to load the arena. That is a config choice, not a bug.

`GlobalDefaultGameMode` points at a **Blueprint** (`BP_ThirdPersonGameMode`), not the C++ class —
which is why the pawn class, controller class and the C++ `AWishCraftGameMode` wiring all converge
there.

Also present: `ActiveClassRedirects` mapping the template's `TP_ThirdPerson*` classes to
`WishCraft*`. These are why renaming a C++ class does not orphan Blueprints that were saved against
the old name.

## 1.5 Config: the PIE settings, and the precedence trap that cost a whole session

For testing you need two players on one machine: a **listen server** with one connecting client.
That is a setting, and it lives in:

```ini
; Config/DefaultEditorPerProjectUserSettings.ini
[/Script/UnrealEd.LevelEditorPlaySettings]
PlayNetMode=PIE_ListenServer
PlayNumberOfClients=2
```
*(Source)*

**That file is not necessarily the one the editor reads.** There are two layers:

| File | Role |
|---|---|
| `Config/DefaultEditorPerProjectUserSettings.ini` | the *default* — version-controllable, ships with the project |
| `Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini` | the *live* per-user state — written on editor exit, **takes precedence** |

An entire verification session was lost to this. Editing only the `Config/` file changed nothing,
because `Saved/Config/WindowsEditor/` still said `PIE_Standalone` / `1`. The restart "proved" the
fix had not worked: the editor reopened and still reported `PlayNumberOfClients = 1`.

Two lessons, both worth remembering:

1. **Ini precedence is live-wins.** If a setting seems to ignore your edit, check for a competing
   key under `Saved/Config/`.
2. **Some settings are only absorbed at process start.** `ULevelEditorPlaySettings` is a CDO read
   during editor startup, so a mid-session ini edit is inert. That is why "restart and see" was
   the right instinct — it just needed the right file.

A third wrinkle, because it recurs: `PlayNetMode` is a `TEnumAsByte<EPlayNetMode>` and is not
pythonable — `set_editor_property("PlayNetMode", 1)` fails with *"Failed to convert type 'int' to
property 'PlayNetMode'"*, and reading it fails with *"Cannot pythonize '0' as 'EnumBase'"*.
`PlayNumberOfClients` **is** writable on the live CDO. So the practical split is: client count via
script, net mode via the UI. *(Measured)*

## 1.6 How the code actually gets compiled

Worth being precise, because "it compiled" and "the running editor has it" are different claims.

**UnrealBuildTool (UBT)** is the real compiler driver — not an IDE, not Live Coding. It reads the
`Target.cs` and `Build.cs` files above and produces
`Binaries/Win64/UnrealEditor-WishCraft.dll`.

| Route | What it does | When the editor must close |
|---|---|---|
| `recompile_unreal_project` | runs UBT for the **Editor** target, incremental | must be closed (or already down) |
| `rebuild_and_restart_editor` | save → close → UBT → reopen on the same level | yes, by definition |
| `trigger_live_coding` | hot-patches *bodies* into the running process as `UnrealEditor-WishCraft.patch_N.dll` | no |

**The distinction that solves arguments:** Live Coding never rewrites
`UnrealEditor-WishCraft.dll`. It emits a separate `patch_N` image and re-instances objects. So you
can tell which route built the running code by comparing timestamps: if the main DLL is newer than
the newest `.cpp`, UBT built it; if there is a recent `patch_0.dll`, Live Coding patched it.

**And the failure mode that shaped this build:** Live Coding crashed the editor with
`LIVECODING_COULDNT_PATCH` while reloading the `/Script/WishCraft` package. It built the patch
successfully and then faulted during the *reload* — the part that has to re-instance
`UCLASS`/`USTRUCT` layouts. Conclusion, now a working rule:

> Function-body-only change → Live Coding is fine. Anything touching a `UPROPERTY`, `UFUNCTION`,
> class layout or constructor → full UBT rebuild, editor closed.

One more constraint that appears the moment you run an assistant *inside* the editor:
`rebuild_and_restart_editor` cannot close the editor without closing the session that is driving
it. Body changes go through Live Coding; structural changes need you to close and reopen.

---

# PART 2 — Networking, as this project actually uses it

This is the part to be able to explain without notes.

## 2.1 Three ways data crosses a network, and when to use each

```mermaid
flowchart TB
    subgraph P["1. Replicated property (state)"]
        A1["server sets value"] --> A2["engine sends it when it changes"]
        A2 --> A3["client runs OnRep"]
    end
    subgraph R["2. RPC (event)"]
        B1["call a marked UFUNCTION"] --> B2["engine sends a one-shot bunch"]
        B2 --> B3["target machine runs _Implementation"]
    end
    subgraph D["3. Derived state (nothing sent)"]
        C1["same replicated input"] --> C2["same computation everywhere"]
    end
    style P fill:#4a90e2,stroke:#2171b5,color:#fff
    style R fill:#b87700,stroke:#ff8c00,color:#fff
    style D fill:#41a161,stroke:#228b22,color:#fff
```

| You need | Use | Why |
|---|---|---|
| Everyone must agree on a value | **replicated property** | it is *state*: it lands for a late joiner and re-sends if dropped |
| Something must *happen* once, visibly | **RPC** | it is an *event*: it has a direction and a reliability choice |
| Everyone can work it out from data they already have | **derived state** | costs zero bandwidth |

The single most useful question when designing anything networked:

> **Is this a fact or a moment?** Fact → property. Moment → RPC.

This project got that question wrong once, and the symptom was a player was invisible to himself
and visible to everyone else. See P1.

## 2.2 Property replication, mechanically

Four pieces, and all four are required:

```cpp
// 1. the property, marked, with an optional OnRep
UPROPERTY(ReplicatedUsing = OnRep_Invisible, BlueprintReadOnly, Category="Ability")
bool bInvisible = false;

// 2. the registration, one per class that owns replicated properties
void AWishCraftCharacter::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(AWishCraftCharacter, bInvisible);
}

// 3. the callback, which must be UFUNCTION() and take no parameters
UFUNCTION()
void OnRep_Invisible();

// 4. for components, replication is off by default and must be switched on
UHealthComponent::UHealthComponent()
{
    SetIsReplicatedByDefault(true);
}
```
*(Source — `WishCraftCharacter.h:125`, `.cpp:64-69`, `HealthComponent.cpp:9`)*

**The traps:**

- `DOREPLIFETIME` without `UPROPERTY(Replicated)` sends nothing. `UPROPERTY(Replicated)` without
  `DOREPLIFETIME` sends nothing. Both, or neither.
- `OnRep` does **not** run on the server when the server sets the value. That is why
  `AWishCraftCharacter::SetInvisible` sets the flag **and** calls `ApplyInvisibleVisual()` itself:

```cpp
void AWishCraftCharacter::SetInvisible(bool bNewInvisible)
{
    if (!HasAuthority() || bInvisible == bNewInvisible) return;
    bInvisible = bNewInvisible;
    ApplyInvisibleVisual();   // server applies locally; OnRep handles every client
}
```
  *(Source, `.cpp:71-81`)*
- `OnRep` must be **idempotent** — it can fire on join, out of order relative to other actors, and
  more than once. `ApplyInvisibleVisual()` reads the flag and sets visibility from it, rather than
  toggling. Correct by construction.
- `SetIsReplicatedByDefault(true)` is a *component* thing. An actor uses `bReplicates = true`.
  Forget the component one and the component silently never replicates.

## 2.3 RPCs, mechanically

```cpp
// Declaration — the specifier alone decides the direction
UFUNCTION(Server, Reliable)
void ServerActivateRPC(FName Token);

// Definition — the suffix is mandatory
void UAbilityLoadoutManager::ServerActivateRPC_Implementation(FName Token) { ... }

// A multicast has no parameters to address, so the body is the _Implementation
UFUNCTION(NetMulticast, Reliable)
void MulticastAbilityEffect(FName Token, bool bStarting);
```
*(Source — `AbilityLoadoutManager.h:92-96`, `WishCraftCharacter.h:121-122`)*

**Three rules that cause most RPC bugs:**

1. **`_Implementation` is the body.** Declare `Foo()`, implement `Foo_Implementation()`. Get this
   wrong and it compiles and never runs.
2. **The caller and the runner are different machines.** Code after `ServerActivateRPC(Token)` runs
   immediately on the caller, *before* the server has done anything. Anything that must be true
   after the server's decision has to come back as replicated state.
3. **A Server RPC needs a valid owning connection.** If the actor has no owning connection, the
   engine logs *"No owning connection"* and drops it. This is exactly what a PlayerController's
   RPC looks like from a client that doesn't own it.

**Reliable vs Unreliable** — the project uses both, deliberately:

| RPC | Reliability | Reason |
|---|---|---|
| `ServerActivateRPC` | Reliable | a lost activation is a player pressing a button and nothing happening |
| `ServerSetLoadoutRPC` | Reliable | the rule change must land or the match silently diverges |
| `MulticastAbilityEffect` | **Reliable** | a dropped "off" leaves a stuck visual on someone else's screen |
| `AAbilityProjectile::MulticastExplode` | Unreliable | purely decorative, and the projectile is destroyed anyway |

The brief originally said cosmetics should be **unreliable multicast**. That was changed for the
ability cosmetic (D6) and documented as a deliberate tradeoff, not an oversight.

## 2.4 The ownership rule that decided the whole architecture

This one fact caused the project's hardest bug, so learn it properly.

**`APlayerController` is `bOnlyRelevantToOwner = true`.** *(Inferred from `AActor`/`AController`
defaults; confirmed behaviourally.)* Two consequences:

1. A `PlayerController` exists on the **owning connection only**. The host's controller does not
   exist in a client's world *at all*.
2. Any RPC on a `PlayerController`, or on a component owned by one, can only ever reach that one
   connection. A `NetMulticast` there is not "multicast" — it is effectively "local".

**`APawn` is not.** A pawn replicates to every client that can see it, which is why it is the
correct carrier for anything a third party must observe.

```mermaid
flowchart LR
    M["Multicast on<br/>UAbilityLoadoutManager<br/>(component of PlayerController)"]
    G{"IsNetRelevantFor<br/>per connection"}
    M --> G
    G -->|"the connection that owns this PC"| OK["delivered"]
    G -->|"every other client"| DROP["dropped - PC is owner-only relevant"]
    style OK fill:#41a161,stroke:#228b22,color:#fff
    style DROP fill:#d15858,stroke:#e74c3c,color:#fff
    style M fill:#4a90e2,stroke:#2171b5,color:#fff
```

**And a subtler consequence, which is why `BP_OnAbilityEffect` exists.** Ability components are
derived from the manager's `Loadout`, and the manager only exists where its PC does. So a client
that does not own a pawn has **no ability component for that pawn**. `GetLoadoutManager()` returns
`nullptr` on a viewer:

```cpp
UAbilityLoadoutManager* AWishCraftCharacter::GetLoadoutManager() const
{
    if (const AWishCraftPlayerController* OwningController = Cast<AWishCraftPlayerController>(GetController()))
        return OwningController->GetLoadoutManager();
    return nullptr;    // a remote pawn has no Controller on a client
}
```
*(Source, `.cpp:182-190`)*

That null is not a bug — it is the architecture. It is why the cosmetic hook that viewers can use
lives on the **character** (`BP_OnAbilityEffect`), not on the ability component (`BP_OnCosmetic`).

## 2.5 Net modes and the test rig

| Mode | Meaning |
|---|---|
| `NM_Standalone` | single player, no net driver |
| `NM_ListenServer` | this process is server *and* has a local player |
| `NM_Client` | this process is a client of someone else |
| `NM_DedicatedServer` | server only, no local player (cut from this project) |

The PIE rig: **listen server + 1 client**, so the editor opens two preview windows. The server
world is `UEDPIE_0_<Map>`, the client world is `UEDPIE_1_<Map>`. The client's connection to the
local server shows up as `IpConnection_1` on port `17777`. *(Measured)*

**Two things that will waste your time in PIE:**

- **Pawn actor names flip between launches.** The host might be `..._C_0` on the server and
  `..._C_1` in the client world, then reversed next run. Never identify a pawn by name, position or
  index-in-list. Resolve it through `get_player_controller(world, i)` / `get_player_pawn(world, i)`.
  *(Measured, repeatedly.)*
- **The two worlds are separate `UWorld` objects.** Reading "the" pawn's health gives you one
  machine's opinion. `unreal.get_game_world` does not exist; PIE worlds must be reached by object
  path, e.g. `unreal.load_object(None, "/Game/Maps/UEDPIE_1_Lvl_Arena.Lvl_Arena")`.

---

# PART 3 — The systems, file by file

## 3.1 The data vocabulary — `Abilities/AbilityTypes.h`

Four types, and the split between them *is* the design:

| Type | Contains | Replicates? |
|---|---|---|
| `EAbilityCategory` | `Offense` / `Defense` / `Mobility` — the only axis validation caps | n/a |
| `FAbilityTokenDefinition` | one row of the table: display name, category, component class, defaults, ranges, keywords | no — it is the *menu*, not the order |
| `FAbilitySpec` | `FName Token` + `TMap<FName,float> Params` (already clamped) | **yes** — this is what is equipped |
| `FAbilityState` | `Token`, `LastActivationServerTime`, `bActive` | **yes** — this is runtime status |

**A decision with real consequences:** the file stores `LastActivationServerTime`, not a
"cooldown remaining" countdown.

```cpp
UPROPERTY(BlueprintReadOnly)
float LastActivationServerTime = -10000.f;
```
*(Source, `AbilityTypes.h:92`)*

Why: a countdown would need to be re-sent or decremented everywhere. A *timestamp* is a fact, and
remaining time becomes a pure function both machines compute identically:

```cpp
const float Elapsed = GetServerTimeSeconds() - State->LastActivationServerTime;
return FMath::Max(0.f, *CooldownParam - Elapsed);
```
*(Source, `AbilityLoadoutManager.cpp:121-122`)*

**And the detail that makes it correct rather than approximate:** `GetServerTimeSeconds()` reads
`AGameStateBase::GetServerWorldTimeSeconds()`, **not** `GetWorld()->GetTimeSeconds()`.

```cpp
float UAbilityLoadoutManager::GetServerTimeSeconds() const
{
    const AGameStateBase* GameState = GetWorld()->GetGameState();
    return GameState->GetServerWorldTimeSeconds();
}
```
*(Source, `.cpp:59-69`)*

Local world time differs per machine and would drift; the GameState's server world time is
replicated and shared. Using the wrong one produces cooldowns that are subtly wrong *only* on
clients — the kind of bug that survives casual testing.

## 3.2 The token table — `Abilities/AbilityTokenTable.h/.cpp` + `/Game/Sandbox/DA_AbilityTokens`

A `UPrimaryDataAsset` holding `TMap<FName, FAbilityTokenDefinition> Tokens`, with
`FindToken`, `BuildDefaultSpec`, `GetTokenNameList`.

Populated asset contents *(Measured)*:

| Token | Category | Params |
|---|---|---|
| `Fireball` | Offense | Damage 20, ProjectileSpeed 2500, Cooldown 1.5 |
| `Invisibility` | Defense | Duration 5, Cooldown 8 |
| `SpeedBoost` | Mobility | Duration 5, Multiplier 1.6, Cooldown 6 |

**Why a data asset rather than a class per ability:** adding an ability is one component class plus
one table row. The same table feeds the parser, the validator *and* the ability's own defaults, so
the numbers cannot drift apart.

**A convenience with a cost:** the manager loads the table by hard-coded path in `BeginPlay`, so
there is no Blueprint wiring step:

```cpp
TokenTable = LoadObject<UAbilityTokenTable>(nullptr, TEXT("/Game/Sandbox/DA_AbilityTokens.DA_AbilityTokens"));
```
*(Source, `.cpp:26`)*

Tradeoff: it works with zero setup, and it is a hard string dependency invisible to the Content
Browser. Move or rename the asset and the system fails at runtime with a warning, not at compile
time.

## 3.3 The loadout manager — the heart of the design

`UAbilityLoadoutManager : UActorComponent`, created as a **constructor default subobject** on
`AWishCraftPlayerController`:

```cpp
AWishCraftPlayerController::AWishCraftPlayerController()
{
    AbilityLoadoutManager = CreateDefaultSubobject<UAbilityLoadoutManager>(TEXT("AbilityLoadoutManager"));
}
```
*(Source, `WishCraftPlayerController.cpp:18`)*

**Why the PlayerController, and why a default subobject:**

- The PC is the one actor guaranteed to exist locally for each player, and it **survives the pawn
  dying and respawning**. Loadout and cooldowns therefore do not reset on death.
- A default subobject is replicated as part of its owner automatically. Spawning it at runtime is
  the hard way — subobject replication, timing, and ownership all become your problem.
- Its constructor calls `SetIsReplicatedByDefault(true)` *(Source, `.cpp:16`)* — required, or
  nothing below replicates.

### What replicates

```cpp
UPROPERTY(ReplicatedUsing = OnRep_Loadout) TArray<FAbilitySpec>  Loadout;
UPROPERTY(ReplicatedUsing = OnRep_State)    TArray<FAbilityState> States;

void UAbilityLoadoutManager::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
    Super::GetLifetimeReplicatedProps(OutLifetimeProps);
    DOREPLIFETIME(UAbilityLoadoutManager, Loadout);
    DOREPLIFETIME(UAbilityLoadoutManager, States);
}
```
*(Source, `.h:31-35`, `.cpp:42-48`)*

Two properties. That is the entire replicated surface of the ability system.

**Tradeoff to know for an interview:** `DOREPLIFETIME` without a condition re-sends the **whole
array** on any change. With three tokens that is a few dozen bytes and completely irrelevant. At
scale you would use `DOREPLIFETIME_CONDITION` and/or a dirty-flag scheme. Knowing *when* this
becomes a problem is the point.

### How components are derived (the central mechanism)

```cpp
UAbilityComponentBase* Component = NewObject<UAbilityComponentBase>(
    Pawn,                         // outer = the pawn, so they die with it
    Definition->ComponentClass,   // the token's class
    NAME_None,
    RF_Transient);                // transient: not saved, not owned by the map
Component->RegisterComponent();
Component->ConfigureAbility(Spec.Token, *Definition, Spec.Params, this, bAuth);
```
*(Source, `.cpp:185-192`)*

**Why `Pawn` as outer:** components then die with the pawn and are rebuilt on respawn, thanks to
`OnPossess` / `AcknowledgePossession` re-running the refresh. Note the pairing:

```cpp
void AWishCraftPlayerController::OnPossess(APawn* InPawn)             { AbilityLoadoutManager->RefreshAbilityComponents(); }
void AWishCraftPlayerController::AcknowledgePossession(APawn* InPawn) { AbilityLoadoutManager->RefreshAbilityComponents(); }
```
*(Source, `WishCraftPlayerController.cpp:52-70`)*

`OnPossess` fires on the server; `AcknowledgePossession` fires on the owning client. The project
refreshes on both because the pawn may not exist at the moment the loadout arrives — whichever
happens second does the real work.

**`bAuth = Pawn->HasAuthority()`** is passed into every component, so one code path serves both
sides and the component always knows whether it is allowed to act.

### Activation — the full path

```cpp
void UAbilityLoadoutManager::ServerActivateRPC_Implementation(FName Token)
{
    UAbilityComponentBase* Component = FindAbilityComponent(Token);
    if (!Component) return;                       // not equipped

    if (!Component->CanActivate()) return;        // cooldown / precondition

    FAbilityState* State = /* find or add */;
    State->LastActivationServerTime = GetServerTimeSeconds();
    State->bActive = true;

    const float Duration = Component->Activate();  // the effect happens HERE
    PlayEffectOnPawn(Token, true);                 // the visual is broadcast

    if (Duration > 0.f)
        GetWorld()->GetTimerManager().SetTimer(Handle, /* EndActiveAbility */, Duration, false);
    else
        State->bActive = false;                    // instant ability, no timer
}
```
*(Source, `.cpp:294-337`, condensed)*

```mermaid
sequenceDiagram
    participant C as Client
    participant S as Server
    participant V as Other clients
    C->>S: ServerActivateRPC(Token)
    S->>S: CanActivate() - cooldown gate
    S->>S: stamp LastActivationServerTime
    S->>S: Activate() returns Duration
    S->>V: MulticastAbilityEffect(Token, true)
    S-->>C: Loadout + States replicate
    S->>S: timer fires EndActiveAbility
    S->>V: MulticastAbilityEffect(Token, false)
```

**Note what is *not* in that diagram: the client is never told "it worked" by the RPC.** It learns
the outcome by watching replicated state change. That is what makes the system robust to a dropped
packet — the state is the truth, and it re-sends.

`EndActiveAbility` is the mirror image: `Deactivate()`, `bActive = false`, drop the timer handle,
broadcast the "off" cosmetic.

### The vital one-liner

```cpp
void UAbilityLoadoutManager::OnRep_Loadout()
{
    RefreshAbilityComponents();
}
```
*(Source, `.cpp:205-209`, plus a log line)*

That is the **entire client half** of the architecture. The client receives a list of tokens and
rebuilds its own behaviour from it. Nothing else is required.

## 3.4 Ability components — derived, deliberately not replicated

```cpp
/**
 * Lives on the pawn and is derived locally on every machine from the replicated token list.
 * It is deliberately NOT replicated: all shared state travels on UAbilityLoadoutManager.
 */
UCLASS(Blueprintable, BlueprintType, ClassGroup = Abilities)
class UAbilityComponentBase : public UActorComponent
```
*(Source, header comment, lines 12-17)*

**The brief said `replicated UActorComponent`. The implementation made it non-replicated.** That is
the biggest single deviation from the plan and it was right:

| Approach | Cost |
|---|---|
| Replicate the components | subobject replication, per-machine construction order, RPC ownership, and a component on every client for every pawn — expensive and fragile |
| Replicate the *inputs*, derive locally | each machine must derive deterministically from the same table; viewers have no components for remote pawns (a real limitation, handled in P5) |

The contract is five virtuals:

| Function | Runs where | Responsibility |
|---|---|---|
| `CanActivate()` | server | cooldown / precondition gate |
| `float Activate()` | server | do the thing, return active duration (`0` = instant) |
| `Deactivate()` | server | undo the timed effect |
| `HandleCosmetic(bool)` | server **and** clients | visual/movement half |
| `BP_OnCosmetic(bool)` | anywhere | Blueprint VFX/sound hook |

Each subclass then stays tiny. `Ability_Invisibility::Activate` just returns the duration
*(Source, `.cpp:6-10`)*, and its visual half delegates to replicated state:

```cpp
void UAbility_Invisibility::ApplyInvisibility(bool bInvisible)
{
    if (AWishCraftCharacter* WishCharacter = Cast<AWishCraftCharacter>(GetAvatarPawn()))
        WishCharacter->SetInvisible(bInvisible);
}
```
*(Source, `.cpp:18-28`)*

**`Ability_SpeedBoost` is the interesting one**, because of a UE 5 fact that surprises people:

```cpp
// MaxWalkSpeed is not replicated in UE5, so every machine applies the same change locally
// and the server keeps authority over the resulting movement.
ApplySpeed(bStarting);
```
*(Source, `Ability_SpeedBoost.cpp:13-20`)*

`UCharacterMovementComponent::MaxWalkSpeed` is a local tuning value, not a replicated property. The
server's movement simulation is authoritative, but if only the server raised the value, the owning
client's predicted movement would disagree and feel like rubber-banding. So the multiplier is
applied **on every machine through `HandleCosmetic`**. Baseline is `500.f`
*(Source, `WishCraftCharacter.cpp:41`)*, ×1.6 = `800`, which is exactly what was measured.

*This put real weight on the transport being reliable* — a dropped "off" would leave one machine at
800 forever. That is the argument for `Reliable` on the cosmetic multicast (D6).

## 3.5 The character — replicated state plus the cosmetic transport

`AWishCraftCharacter` owns two networking responsibilities:

**(a) The invisibility fact.**

```cpp
UPROPERTY(ReplicatedUsing = OnRep_Invisible, BlueprintReadOnly, Category="Ability")
bool bInvisible = false;
```
*(Source, `.h:125-126`)*

**(b) The cosmetic moment.**

```cpp
UFUNCTION(BlueprintCallable) void PlayAbilityEffect(FName Token, bool bStarting);        // server-only entry
UFUNCTION(NetMulticast, Reliable) void MulticastAbilityEffect(FName Token, bool bStarting); // the wire
UFUNCTION(BlueprintImplementableEvent) void BP_OnAbilityEffect(FName Token, bool bStarting); // viewer hook
```

```cpp
void AWishCraftCharacter::PlayAbilityEffect(FName Token, bool bStarting)
{
    if (!HasAuthority()) return;   // only the server starts a multicast
    MulticastAbilityEffect(Token, bStarting);
}

void AWishCraftCharacter::MulticastAbilityEffect_Implementation(FName Token, bool bStarting)
{
    // server + owning client have a component; viewers do not
    if (UAbilityLoadoutManager* LoadoutManager = GetLoadoutManager())
        if (UAbilityComponentBase* Component = LoadoutManager->FindAbilityComponent(Token))
            Component->HandleCosmetic(bStarting);

    BP_OnAbilityEffect(Token, bStarting);   // what a third party can actually use

    UE_LOG(LogSandbox, Log, TEXT("Ability effect %s %s for %s (%s)"),
        bStarting ? TEXT("on") : TEXT("off"), *Token.ToString(), *GetNameSafe(this),
        HasAuthority() ? TEXT("server") : TEXT("client"));
}
```
*(Source, `.cpp:97-126`)*

**Read that body as three layers of a decision:**
1. If I have per-ability logic and a component (`HandleCosmetic`), run it — covers server and owner.
2. Always fire the token-level Blueprint event — covers viewers with no component.
3. Log which machine ran it, in a way a log reader can tell apart. That last line is not decoration;
   it is what made the transport provable (P4).

**Why `Reliable`:** the on/off pair must not desync. A dropped "on" is a missing effect; a dropped
"off" is a permanently wrong visual. Low frequency (three abilities, cooldown-gated) makes the cost
negligible.

## 3.6 Input → activation, the complete trace

Worth memorising, because "which machine runs this?" is the standard interview probe.

```
[Owning client]  key press (LMB / RMB / LShift)
   ↓ EnhancedInputComponent::BindAction -> AWishCraftCharacter::OnAbilityOffense
   ↓ ActivateCategory(EAbilityCategory::Offense)
   ↓ GetLoadoutManager()                   (present: this is the local player's own PC)
   ↓ UAbilityLoadoutManager::RequestActivateCategory
   ↓ FindTokenByCategory -> RequestActivateToken
   ↓ ServerActivateRPC(Token)              ← crosses the network here
[Server]
   ↓ ServerActivateRPC_Implementation
   ↓ CanActivate → Activate()  → spawns projectile / sets flag / raises speed
   ↓ PlayEffectOnPawn → AWishCraftCharacter::PlayAbilityEffect → MulticastAbilityEffect
   ↓ timer → EndActiveAbility → Deactivate + "off" cosmetic
[Everyone]
   ↓ replicated Loadout/States/bInvisible + the multicast
```

Bindings are **guarded**, which matters because the input assets live on a Blueprint:

```cpp
if (AbilityOffenseAction)
    EnhancedInputComponent->BindAction(AbilityOffenseAction, ETriggerEvent::Started, this, &AWishCraftCharacter::OnAbilityOffense);
```
*(Source, `.cpp:156-174`)*

Why guarded: `AWishCraftCharacter` is `UCLASS(abstract)` and the `UInputAction*` assets are assigned
on `BP_ThirdPersonCharacter`. An unassigned action is then a *no-op* rather than a null-pointer
crash on spawn. Small detail, but it is the difference between "input not working" and "game won't
launch".

## 3.7 Health and damage

`UHealthComponent` on the character, replicated, server-gated:

```cpp
void UHealthComponent::ApplyDamage(float Amount, AActor* DamageInstigator)
{
    if (!GetOwner() || !GetOwner()->HasAuthority() || bDead || Amount <= 0.f) return;
    Health = FMath::Max(0.f, Health - Amount);
    OnHealthChanged.Broadcast(Health, MaxHealth);
    if (Health <= 0.f) { bDead = true; OnDeath.Broadcast(DamageInstigator); }
}
```
*(Source, `.cpp:30-48`)*

`AAbilityProjectile` is the only caller in practice, and it is server-authoritative at every step:

```cpp
bReplicates = true;
SetReplicateMovement(true);          // clients see it fly without a per-frame RPC
...
void HandleOverlap(...) { if (!HasAuthority() || ...) return; Explode(OtherActor); }
void Explode(AActor* DirectHitActor)
{
    if (!HasAuthority()) return;
    TargetHealth->ApplyDamage(Damage, GetInstigator());
    MulticastExplode();              // cosmetic: hide mesh + disable collision on every machine
    Destroy();
}
```
*(Source, `.cpp:15-16, 84-119`)*

**The client never damages anyone.** It reports an overlap on its copy of the projectile, but
`HasAuthority()` is false there, so it does nothing. The server's copy does the overlap test and
applies damage. That is what makes damage un-fakeable.

**`SetReplicateMovement(true)`** is the cheaper alternative to sending position every frame: the
movement component replicates transform with its own throttling, so the client sees a smooth
projectile for almost free.

## 3.8 Match flow

`AWishCraftGameMode` — server-only by nature (a client has no GameMode at all).

```cpp
void AWishCraftGameMode::HandlePlayerDeath(APawn* DeadPawn, AActor* Killer)
{
    APlayerController* VictimController = Cast<APlayerController>(DeadPawn->GetController());
    APawn* KillerPawn = Cast<APawn>(Killer);
    APlayerController* KillerController = KillerPawn ? Cast<APlayerController>(KillerPawn->GetController()) : nullptr;

    if (KillerController && KillerController != VictimController) { Scores...++; if (>= ScoreLimit) bMatchOver = true; }
    if (bMatchOver || !VictimController) return;

    FTimerHandle RespawnHandle;         // local: one timer per death, no shared handle
    GetWorldTimerManager().SetTimer(RespawnHandle, ..., FMath::Max(0.1f, RespawnDelay), false);
}
```
*(Source, `.cpp:17-59`, condensed)*

And the respawn replaces the pawn **outright**:

```cpp
PlayerController->UnPossess();
DeadPawn->Destroy();        // clears ability components, health, timers
RestartPlayer(PlayerController);
NewHealth->ResetHealth();
```
*(Source, `.cpp:66-95`)*

**Why destroy rather than revive:** every derived thing (components, timer handles, speed buff)
hangs off that pawn. Destroying it is the cleanest possible reset, and the PC keeps the loadout, so
the player comes back with the same abilities. That is the design paying off.

**The local `FTimerHandle` is a real detail, not style:** a member handle would be overwritten by a
second concurrent death, orphaning the first respawn. Because the timer manager keeps its own
registration, a local handle is safe and is the correct idiom here.

Two observed behaviours worth knowing: respawn measured at **1.95 s** against `RespawnDelay = 2`
*(Measured)*, and **the corpse persists after match over** because `RespawnPlayer` is gated on
`!bMatchOver`. Designed, not a defect.

## 3.9 The rules layer

**Parser** (`Rules/RuleParserLibrary.cpp`) — deliberately dumb, and the header says so:

```cpp
Normalize:  lowercase, strip spaces / '-' / '_'
FindTokenInText:  haystack.Contains(token)  ||  haystack.Contains(keyword)
ParseRuleText:  loop the whole table, AddUnique, then Sort(FNameLexicalLess())
```
*(Source, `.cpp:7-77`)*

Three consequences, all of which someone will eventually report as a bug:

- **De-duplicated and alphabetically sorted.** `"Invisibility,Invisibility"` applies **one**
  Invisibility. Correct, and it means the Defense cap is unreachable through the text parser — only
  through the raw-token path. *(Measured: case 5.)*
- **Substring, not word-boundary.** `"fire"` matches inside `"fireball"`; a keyword like `"fast"`
  would match `"breakfast"`. Acceptable for a stub, not for a real parser.
- **Sort order decides `FindTokenByCategory`.** Alphabetical means the equipping order the user
  typed is lost. Deterministic, which is what matters.

**Validator** (`Abilities/LoadoutValidator.cpp`) — four stages, and **the order is load-bearing**:

1. Unknown tokens → `"'X' is not an ability I know. Try: ..."`
2. **Category caps** (`GetCategoryCap` returns `1` for Defense, `99` otherwise)
3. Duplicates
4. Build + clamp params, logging every clamp

Caps are checked **before** duplicates so the message names the rule that was actually broken.
That ordering is why `RulesRaw Invisibility,Invisibility` returns *"Only 1 Defense ability is
allowed, but the rules asked for 2."* rather than "listed more than once". *(Measured.)*

`FLoadoutValidationResult` defaults to `bValid = false`, so **every early return is a rejection** and
only the last line sets it true. That is a defensive default worth copying.

### The rules layer's successor — the intent model

`ParseRuleText` is synchronous and returns `bool`, so a model round trip **cannot live inside it**.
The seam that makes the model possible already existed by accident:

```cpp
ApplyRuleText(RuleText, Error)   // :106  parse + apply   (sync — stays as the fallback)
ApplyRawTokens(Tokens, Error)    // :127  apply only       (sync, already async-friendly)
```

Intended flow: `Rules("fireballs only")` → async HTTP POST → callback → `ApplyRawTokens`. Nothing
downstream changes, and `ULoadoutValidator` still gates the result, so the model can only
*propose*. Two properties are worth keeping for that reason: a hallucinated ability becomes the
existing rejection message rather than a broken match, and the parser stays as the offline
fallback rather than being deleted.

The model itself is an offline toolchain in `Tools/intent_model/` — corpus generator, trainer,
evaluator, FastAPI service — and has its own tutorial: **`Docs/IntentModelTutorial.md`**. The
measurements behind it are **PART 11** of this document.

Honest status as of P13: the model **loses to this parser** on both corpora (0.501 vs 0.652
token-set exact on `test`, 0.500 vs 0.700 on `golden`), and no C++ transport exists yet, so
`ParseRuleText` is still what runs in game. Its direction head now beats a constant (0.637 vs
0.447), which is the first part of it that does.

## 3.10 The host UI

`WBP_RuleInput`, created lazily and only on the host:

```cpp
if (HasAuthority() && IsLocalPlayerController())
{
    SetRuleWidgetVisible(true);
}
```
*(Source, `WishCraftPlayerController.cpp:45-49`)*

Both halves of that condition matter: only the **server** may change rules (authority), and only
the **local** player has a screen to show a widget on. It also sets `FInputModeGameAndUI` and shows
the cursor on open, restoring `FInputModeGameOnly` on close — otherwise the panel steals movement
input.

Three `UFUNCTION(Exec)` entry points (`Rules`, `RulesRaw`, `RuleUI`) exist purely because
headless/automated testing cannot type in a widget or a console. `RulesRaw` bypasses the parser
entirely, which is what makes negative tests possible.

---

# PART 4 — The bugs, and what they teach

## P1 — Invisible on the server, visible to everyone else

**Symptom.** Host activates Invisibility. Reading visibility in both worlds:

```
SERVER  BP_ThirdPersonCharacter_C_0  meshVisible=False
CLIENT  BP_ThirdPersonCharacter_C_1  meshVisible=True    (the same pawn)
```
*(Measured)*

**First design.** `ServerActivateRPC` → `MulticastAbilityEffect` on the **manager** →
`HandleCosmetic` → mesh visibility.

**Cause.** The manager is a component of the PlayerController, and a PC is owner-only relevant. The
host's PC does not exist in the client world at all, so the multicast had nowhere to land — not for
third parties, and not even for the host's own client. See 2.4.

**Fix.** Move the *fact* to the character as replicated state (`bInvisible` + `OnRep_Invisible`),
and have the ability write to it via `SetInvisible`.

**Tradeoff.** The ability no longer owns its own visual; the character does. A small encapsulation
loss for a state that cannot desync.

**Evidence.** The log pairs, one line per world, one replication interval apart:

```
09:46:44.782  Invisibility on for BP_ThirdPersonCharacter_C_0   (server)
09:46:44.813  Invisibility on for BP_ThirdPersonCharacter_C_1   (client world)
```
*(Measured)*

**The meta-lesson.** A boolean that both machines must agree on is a **fact**, and facts belong in
replicated properties. Reaching for a multicast first is the natural mistake.

## P2 — `C4099: 'FLifetimeProperty': type name first seen using 'class' now seen using 'struct'`

The forward declaration for `GetLifetimeReplicatedProps` was written as `struct FLifetimeProperty;`.
The engine declares it as a `class`.

**Fix.** `class FLifetimeProperty;` — now visible at `WishCraftCharacter.h:17`.

**Lesson.** When forward-declaring an engine type, copy its tag exactly. You never include the
engine header that would tell you, so the compiler is your only check and it only speaks once.

## P3 — Build tooling failing in three different ways

1. **`rebuild_and_restart_editor` → `refused` with an empty error list.** Its dirty-package pre-check
   needs a live bridge; during an editor reopen there isn't one. It fails safe, which looks
   identical to a genuine refusal unless you check `get_editor_status`.
2. **"No Unreal Editor is running."** The reopen never completed. With the editor down,
   `recompile_unreal_project` is the reliable path: `status: success`, `exit_code: 0`, `9.1 s`.
3. **Docked inside the editor**, `rebuild_and_restart_editor` can never be used — closing the editor
   closes the session driving the build. Body changes go through Live Coding.

**The rule now used:**

| Change | Route |
|---|---|
| function body / `.cpp` only | Live Coding |
| new `UPROPERTY` / `UFUNCTION` / class / module | full rebuild, editor closed |
| editor already down | `recompile_unreal_project` then launch |

## P4 — The false positive: "the multicast is definitively broken"

This is the most valuable bug in the project, because the *diagnosis* was the bug.

**Symptom.** After P1's fix, ability cosmetics still logged only the server line — even with the
client world provably alive and logging 30 ms later via the separate `OnRep` path.

**Wrong turn 1 — relevancy hypothesis.** The client's copy of the pawn measured as a textbook
simulated proxy: `Role=SimulatedProxy`, `RemoteRole=Authority`, `bReplicates=true`,
`bOnlyRelevantToOwner=false`, `NetDormancy=DormAWAKE`, `NetCullDistanceSquared=225000000`, pawns
3600 uu apart. Everything correct — so relevancy remained plausible.

**Wrong turn 2 — instrument the send.** In `UNetDriver::ProcessRemoteFunction` the multicast loop
is gated by exactly one silent condition (approximately `NetDriver.cpp:8243` in 5.8):

```cpp
if (Actor->IsNetRelevantFor(Viewer.InViewer, Viewer.ViewTarget, Viewer.ViewLocation) || ...)
```

A temporary diagnostic was compiled into `AWishCraftCharacter::PlayAbilityEffect` printing the
connection count, each `ViewTarget`, and the relevance result. It returned:

```
 1 client connection(s)
 conn=IpConnection_1 viewTarget=BP_ThirdPersonCharacter_C_1 relevant=1
```
*(Measured)*

**Relevance passed.** The RPC was being handed to the send path: not filtered there.

**The real cause — a measurement artifact.** Raising `LogNet` to `VeryVerbose` gave it away:

```
LogNet: VeryVerbose: GetFunctionCallspace ScriptExecutionInEditor: ServerActivateRPC
LogNet: VeryVerbose: GetFunctionCallspace ScriptExecutionInEditor: MulticastAbilityEffect
```
*(Measured)*

`ScriptExecutionInEditor` means *run this locally, do not network it*. **Any net function invoked
during a script call takes that path.** Every test so far had been driven from Python:

```
server_set_loadout(...) → request_activate_token(...) → ServerActivateRPC → MulticastAbilityEffect
```

One Python call stack. Nothing ever left the process. **The product code was fine; the test was
measuring itself.**

**The correction, and it is worth being blunt about:** the earlier confident claim that the
manager's multicast was "proven broken by log lines" rested on this contaminated measurement and
does not stand as evidence. The structural argument in 2.4 is still sound — but it is *reasoning
from relevance rules*, not measurement.

**How to verify correctly.** Trigger the same call from a **game tick**. The cheapest game-tick
trigger available was the ability's own expiry timer: activate with a short `Duration` and let
`EndActiveAbility` fire the "off" cosmetic.

```
14.19.46.474 [492]  Ability effect off Invisibility for ..._C_0 (server)
14.19.46.476 [492]  Ability effect off Invisibility for ..._C_1 (client)
```
*(Measured, 2 ms apart, on the final build with the diagnostic removed.)*

**The standing rule:**

> **Never verify an RPC from a script call stack.** Verify **state** by reading properties in both
> PIE worlds; verify **events** by triggering them from a game tick (timer, input, overlap). When in
> doubt, read `GetFunctionCallspace`.

**Second-order lesson, and the generalisable one:** a test harness can silently defeat the thing
under test. When a measurement says "the feature is broken", first ask whether the harness can even
express the feature. Two full rebuild cycles went into diagnosing the harness.

## P5 — A viewer has no ability component, so `HandleCosmetic` cannot reach it

`GetLoadoutManager()` walks `GetController()`. On a client, a **remote** pawn has no `Controller`
(PlayerControllers replicate to their owner only), so it returns null. Even if it resolved, that
manager's `AbilityComponents` map is built from *its own* loadout, and `RefreshAbilityComponents` is
driven by `OnRep_Loadout` on the manager that only exists for the local player.

**Fix.** The multicast always fires a token-level hook on the character:

```cpp
UFUNCTION(BlueprintImplementableEvent, Category="Ability", meta = (DisplayName = "On Ability Effect"))
void BP_OnAbilityEffect(FName Token, bool bStarting);
```

**Tradeoff, stated honestly:** third-party visuals key off a token in the character Blueprint rather
than off the ability component. That asymmetry is *forced* by the two decisions above it. The
alternative — replicating every player's loadout to every client so everyone can derive every
pawn's components — costs far more and breaks the D3 invariant.

**Status.** The hook exists and is proven to fire on the viewer. Nothing implements it yet: all three
tokens point at C++ classes, so there is no per-ability VFX to see. Correct plumbing, empty.

## P6 — Testing ergonomics that cost real time

- **Duration windows vs. tool latency.** The shipped Invisibility lasts 5 s — often shorter than the
  gap between two tool calls. Widening it is per-machine:
  `widened = dict(params.items()); widened[unreal.Name("Duration")] = 60.0; comp.set_editor_property("Params", widened)`.
  Widening the server's component does **not** widen the client's; only the server's timer matters.
- **`register_slate_post_tick_callback` registers without error and never fires** in a headless
  editor. A frame-by-frame sampler built on it recorded nothing, silently, and looked like a
  measurement that was "still waiting". Do not trust a callback that has not been observed to fire.
- **`unreal.log` from a tick callback does not reach `WishCraft.log`.** Script logging is captured
  in-process. Anything that must survive must be written to a file.
- **Log rotation** swallowed the evidence for cases 1 and 2 (`11/07/26 09:18:28`). Conclusions
  survive; exact lines do not.
- **Console typing is impossible for automation** (`Tilde` leaves the history buffer empty), so the
  `Exec` functions had to be called through their owning object instead.

## P7 — Pre-existing warnings left in place

```
C4263: 'float UAbilityComponentBase::Activate(void)': member function does not override any base class virtual
C4264: 'void UActorComponent::Activate(bool)': no override available ... function is hidden
```
*(Measured)*

`UAbilityComponentBase::Activate()` shadows `UActorComponent::Activate(bool)`. **`Deactivate()` is
worse**, because it shares the base signature *exactly* and therefore silently overrides the
component-lifecycle function — engine-driven deactivation calls the ability's `Deactivate()`.

It worked, but it was a name collision waiting to confuse someone. **Fixed in P10d** — renamed to
`BeginEffect` / `EndEffect`.

## P8 — PIE would not go multiplayer

Covered in 1.5: the setting had to be written to `Saved/Config/WindowsEditor/`, and `PlayNetMode`
cannot be set from Python at all. The automated verification harness had its own version of this
problem — its PIE start call accepts only action and map, and silently ignores client counts and net
mode, returning `{"running": true}` against a single-client world. So two-client verification has to
start from the editor, not from the harness.

## P9 — The rules reached the host and nobody else

**Symptom.** The host typed `fireballs and invisibility only`, the host's abilities worked, and every
keypress on the client logged `No ability equipped for that category` — including for categories the
host demonstrably had.

**Cause.** `UAbilityLoadoutManager::ServerSetLoadout` (`Abilities/AbilityLoadoutManager.cpp`) was
written as a *per-controller* setter. It validated `Tokens`, then did `Loadout = Result.Specs` on
**`this`** — and `this` is the host's own manager, because
`AWishCraftPlayerController::ApplyRuleText` calls it on the host controller. Nothing in the chain ever
addressed the other player's controller. The rules were world rules being stored per-player, so the
host edited only their own copy and the client's manager stayed empty forever.

What separated "broken RPC" from "the fan-out never existed" was a log line that had nothing to do
with networking: `Derived 0 ability component(s) on BP_ThirdPersonCharacter_C_1 (authority: yes)`.
Player 2's pawn, **on the server, with authority**, deriving nothing. That can only happen if that
manager's `Loadout` is empty on the single machine that owns all the state. No replication was
involved at all.

**Fix**, in two parts, both body-only — no new class members, no new virtual functions, which is what
let it go in through Live Coding instead of needing a full UBT rebuild:

1. `ServerSetLoadout` fans the validated spec list out. After applying to itself it walks
   `GetWorld()->GetPlayerControllerIterator()`, skips its own owner, and for every other
   `AWishCraftPlayerController` writes `Loadout`, rebuilds `States` from it, and calls
   `RefreshAbilityComponents()`.
2. `AWishCraftPlayerController::OnPossess` covers the late joiner: if it has authority, its own
   `Loadout` is empty, and another controller already has one, it copies that list. `States` is left
   empty on purpose so a fresh spawn starts with every cooldown expired.

The write is done directly rather than by calling `ServerSetLoadout` again on the other manager,
because that would re-enter the fan-out once per controller and never terminate.

**Tradeoff 1.** The ruleset now exists on N controllers instead of one, each replicating its own copy
to its own owner. That is redundant bytes for a 2-player prototype, and it is the *wrong shape at
scale*. The honest design is one replicated `TArray<FAbilitySpec>` on the GameState with every manager
reading it. It was not done here because adding a member to `AWishCraftGameMode` changes class layout,
which needs a full rebuild, and a rebuild closes the editor (and this chat with it). The reason is
recorded so the next session inherits it rather than rediscovering it.

**Tradeoff 2.** "The match rules live wherever a manager already has them" is an implicit convention,
not an enforced fact. If every controller ever spawned clean, a late joiner would inherit nothing. The
GameState-owned ruleset removes that ambiguity — another argument for doing it properly.

**Evidence** *(Measured — live 2-client PIE, server world `UEDPIE_0_Lvl_Arena`, client world
`UEDPIE_1_Lvl_Arena`)*:

| Point read | Loadout |
|---|---|
| server pc0, before | `[]` |
| server pc1, before | `[]` |
| server pc0, after `pc0.apply_rule_text(...)` | `['Fireball', 'Invisibility']` |
| **server pc1, after** | **`['Fireball', 'Invisibility']`** |
| client world pc, next script call | `['Fireball', 'Invisibility']` |

Server-side `BP_ThirdPersonCharacter_C_0` **and** `_C_1` both derived `Ability_Fireball` +
`Ability_Invisibility` (previously `_C_1` derived 0), and there were zero `No ability equipped` lines
after the fix.

One timing fact worth keeping: the client read returned `[]` inside the same script that applied the
rule, and the correct values on the next call. A blocking Python call never lets the network tick, so
replicated state cannot be observed in the same call that causes it.

**Python API facts that cost two failed attempts:**

- `UFUNCTION(Exec)` (`Rules`) is **not exposed to Python at all** — `pc.rules(...)` raises
  `AttributeError`. Use the `BlueprintCallable` twin (`ApplyRuleText`).
- A `FString& OutError` out-param is **dropped** from the Python signature. `ApplyRuleText` therefore
  takes exactly one argument, and the bool plus the string come back as a tuple:
  `pc.apply_rule_text("fireballs and invisibility only")`.
- `get_editor_property("Pawn")` on a PlayerController is protected; reading it raises **after** the
  rest of the script has already printed, so it looks like a late failure of a successful run.

---

# PART 5 — Verification methodology

## 5.1 What counts as proof

| Claim | Sound evidence | Unsound evidence |
|---|---|---|
| A property replicates | read it in `UEDPIE_0_` and `UEDPIE_1_` and compare | "the client logged something" |
| An RPC arrives | log with the machine tagged, triggered from a game tick | **any** call driven from a script |
| Damage is server-authoritative | health drops on the server only, and the client's copy follows | a client-side number changing |
| A cooldown works | second activation ignored, third after the window succeeds | "it felt right" |

## 5.2 The rig

```python
srv = unreal.load_object(None, "/Game/Maps/UEDPIE_0_Lvl_Arena.Lvl_Arena")
cli = unreal.load_object(None, "/Game/Maps/UEDPIE_1_Lvl_Arena.Lvl_Arena")
# unreal.get_game_world does not exist in 5.8; PIE worlds are reachable only by object path
```

```python
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
les.load_level("/Game/Maps/Lvl_Arena")
les.editor_request_begin_play()      # honours the PIE ini; returns None
les.editor_request_end_play()
```

Always resolve players positionally, never by name:

```python
host = unreal.GameplayStatics.get_player_controller(srv, 0)
pawn = unreal.GameplayStatics.get_player_pawn(cli, 0)
```

## 5.3 Results

| Case | What it checks | Verdict |
|---|---|---|
| 1 | Loadout replication, client half | **PASS** — client logged `Loadout replicated:` then derived its own component with `(authority: no)` |
| 2 | Cross-client damage (client's fireball hurts the host) | **PASS** — server-authoritative health dropped on the server, client copy followed |
| 3 | Cross-client invisibility, viewer side | **PASS after P1** |
| 4 | SpeedBoost clamp and application | **PASS** — 500 → 800, reverting after 5 s |
| 5 | Validation / `RulesRaw` negative path | **PASS** — duplicate Defense rejected with the category message, loadout untouched |
| 6 | Host UI path end to end | **PASS** — panel → parse → validate → apply |
| 7 | Match flow | **PASS** — score 1/3 → 3/3, respawn at 1.95 s, `bMatchOver`, respawns stop |

Case 1's client half is worth quoting because it is the architecture in one line:

```
Loadout replicated: Invisibility
Ability Invisibility configured on BP_ThirdPersonCharacter_C_0 (authority: no)
```
*(Measured)*

That is `OnRep_Loadout` → `RefreshAbilityComponents` doing exactly what section 3.3 says it does, on
the machine that is not allowed to decide anything.

## 5.4 The one that is still not proven end to end

The `on` cosmetic leg has only been observed replicating when triggered from game code, and no
ability implements `BP_OnAbilityEffect`, so **no human has ever seen another player's ability
effect.** The transport is proven; the content is missing. That gap is why it is listed first in
Part 7.

---

# PART 6 — Interview-ready

## 6.1 The 60-second answer

> "It's a data-driven ability system without GAS. The host's rules become a validated list of
> ability tokens — small structs with clamped parameters — and that list is the only thing that
> replicates, from a component on the PlayerController. Every machine derives its own ability
> components locally from that list, so the network never carries components, only data. Activation
> is a Server RPC, the server validates the cooldown against replicated server timestamps and runs
> the effect; anything that must be seen travels as replicated state on the character, or as a
> reliable multicast for cosmetics. The hard part wasn't the abilities — it was discovering that a
> multicast on a PlayerController-owned component can't reach other clients, and that my own test
> harness was silently making every RPC execute locally, which cost me two rebuild cycles of
> confident misdiagnosis."

## 6.2 Questions you should be able to field

**"Why not GAS?"** It was explicitly cut from the brief. GAS gives you prediction, ability tasks and
a lot of structure, at the cost of a steep learning curve and a lot of boilerplate for three
abilities. Here the whole replicated surface is two arrays and one bool, which is explainable in a
minute — which for a portfolio piece is the point.

**"Why don't the ability components replicate?"** Because behaviour doesn't need to. Replicating
components means subobject replication, construction order, per-machine RPC ownership and a
component on every client for every pawn. Replicating the *inputs* and deriving locally means the
wire only carries `FAbilitySpec`/`FAbilityState`. The cost is that a viewer has no components for a
remote pawn — which is exactly why the viewer hook (`BP_OnAbilityEffect`) is on the character.

**"How do cooldowns work without syncing a timer?"** The server replicates the *timestamp* of the
last activation using `GetServerWorldTimeSeconds()`, not local world time. Remaining time is a pure
function of replicated state, so it needs no RPC and no per-frame update, and late joiners get
correct values immediately.

**"What happens if a packet drops?"** State-based things self-heal — the next replication resends
the boolean or the array. That is why invisibility and speed are state, not events. Events that
matter use `Reliable`. The one genuinely fragile case is a cosmetic on/off pair, which is why the
multicast is Reliable rather than the Unreliable the brief specified.

**"How do you know the client can't cheat?"** It structurally can't, for these operations. Damage is
applied only inside `HasAuthority()` guards; activation is a Server RPC; the client's projectile only
*reports* an overlap, and its copy does nothing. There is no prediction to exploit. There is also no
client-side trust to audit, which is its own simplification.

**"What would you do differently?"** Three things: build the two-client test rig *before* the third
ability, so the ownership trap surfaces early; add a "read current state on join" path alongside the
cosmetic multicast (a late joiner currently sees nothing of an in-progress effect); and rename
`Activate`/`Deactivate` on the ability base on day one.

**"Why train a model when the keyword parser works?"** Because keywords are the part that was
already solved — `FindTokenInText` matches a substring perfectly, and a model that only read
keywords would add nothing. The gap is the three things a *set selector* structurally cannot do:
`everything except invisibility` (a set complement, so no per-pair score can express it), `punchy
but slow fireballs` (two parameters moving opposite ways), and `let me vanish twice as long` (a
comparative has to become a bucket). It emits buckets, not numbers, so it never learns arithmetic —
the mapping and clamping stay in C++ where they are testable. And it only proposes; the validator
is still the only writer. It currently loses on accuracy, which is exactly why the parser remains
the fallback instead of being replaced. Detail: `Docs/IntentModelTutorial.md`.

## 6.3 The honest weaknesses

- No client-side prediction; ability visuals are one RTT behind input. Fine at 3600 uu in a LAN
  demo, visible on a real connection.
- `DOREPLIFETIME` re-sends whole arrays. Irrelevant at three tokens, first thing to fix at scale.
- `FindTokenByCategory` returns the first match, so two Offense abilities makes the second
  unreachable by input. The validator caps Defense at 1 but leaves Offense/Mobility at 99.
- `NotifyStateChanged()` is an empty function that reads like it does something.
- Two-clients only. A late joiner's correctness is argued, not tested.
- **The intent model loses to the keyword parser**: 0.501 vs 0.652 token-set exact on `test`,
  0.500 vs 0.700 on `golden`. It is the only component that can express a parameter at all, but
  it is not a drop-in replacement, and it is not wired into the game.
- **The model's `moved` head is still worse than a constant**: 0.852 against 0.858 for always
  answering `default`. Its direction head *does* beat its constant (0.637 vs 0.447), so the
  remaining problem is knowing when to abstain, not which way to go.

---

# PART 7 — Open items

1. **`BP_OnAbilityEffect` has no implementers.** Plumbing correct, visuals absent. Highest visible
   win: the first per-token VFX is the first thing a human can actually see.
2. ~~**Rename `UAbilityComponentBase::Activate`/`Deactivate`** (P7)~~ — **done in P10d**
   (`BeginEffect` / `EndEffect`).
3. **Late-join behaviour for cosmetics.** A multicast is an event, so a client joining mid-effect
   gets nothing. If that matters, snapshot current active states into the character.
4. **Untested beyond two clients.**
5. **`MulticastAbilityEffect` is Reliable by choice** — revisit if any effect ever runs per-frame.
6. **Clean up the `Variant_*` include paths** in `Build.cs` (template leftovers).
7. **No C++ transport for the intent model.** `WishCraft.Build.cs` has no `HTTP`/`Json` entry, so
   nothing in the game calls `/rules`. The model is measured but not playable; every in-game
   verification case for it is therefore untestable, not failing.
8. **The intent model loses to the keyword parser** — 0.501 vs 0.652 token-set exact. Corpus and
   architecture work, not tuning. See `Docs/IntentModelTutorial.md` §8.
9. ~~**Corpus gap for `max` / `much_lower`**~~ — **fixed in P13.** The split was pinning the
   bucket per family (train always `higher`, test always `max`), so training never saw a clean
   single-parameter direction at all. The bucket is now drawn from the prior in all three splits
   and the split varies only the wording: direction 0.326 → 0.637. Still open from that work:
   `moved` sits just below its constant, and per-parameter polarity is now the top confusion.

---

# PART 8 — File map

| File | Role | Networking responsibility |
|---|---|---|
| `WishCraft.uproject` | project descriptor: engine binding, module, plugins | none |
| `Source/WishCraft.Target.cs` | builds the packaged game | none |
| `Source/WishCraftEditor.Target.cs` | builds the editor DLL | none |
| `Source/WishCraft/WishCraft.Build.cs` | module dependencies; `Engine` is what provides replication | enables all of it |
| `Config/DefaultEngine.ini` | startup map, global GameMode, class redirects | GameMode selection |
| `Config/DefaultEditorPerProjectUserSettings.ini` | PIE listen server + 2 clients | the test rig |
| `Abilities/AbilityTypes.h` | `FAbilitySpec`, `FAbilityState`, categories, `LogSandbox` | defines what replicates |
| `Abilities/AbilityTokenTable.h/.cpp` | the menu of abilities | none (asset, not state) |
| `Abilities/AbilityLoadoutManager.h/.cpp` | replicated loadout + derived components + activation | **the core**: 2 replicated arrays, 2 Server RPCs, 1 derived-state design |
| `Abilities/AbilityComponentBase.h/.cpp` | ability behaviour contract | none — deliberately not replicated |
| `Abilities/Ability_Fireball.cpp` | spawns the projectile | server only |
| `Abilities/Ability_Invisibility.cpp` | routes the visual to `SetInvisible` | delegates to replicated state |
| `Abilities/Ability_SpeedBoost.cpp` | local movement multiplier | applied per-machine (MaxWalkSpeed is not replicated) |
| `Abilities/AbilityProjectile.h/.cpp` | projectile, overlap, damage | `bReplicates`, `SetReplicateMovement`, `MulticastExplode` |
| `Abilities/HealthComponent.h/.cpp` | health | replicated `Health` + `OnRep_Health`, authority-gated damage |
| `Abilities/LoadoutValidator.cpp` | 4-stage validation | none |
| `Rules/RuleParserLibrary.cpp` | stub text → tokens | none |
| `Tools/intent_model/` | offline intent model: corpus, trainer, evaluator, FastAPI service | none — **not wired in**; no `HTTP`/`Json` in `Build.cs` yet |
| `WishCraftCharacter.h/.cpp` | player pawn | `bInvisible` + `OnRep`, `MulticastAbilityEffect`, `BP_OnAbilityEffect` |
| `WishCraftPlayerController.h/.cpp` | owns the manager, rule UI, `Exec` hooks | owner of the replicated loadout |
| `WishCraftGameMode.h/.cpp` | scoring, respawn, match over | server-only |

**Invariants — break these and the bugs come back:**

- Ability components stay **non-replicated** and derived locally from the manager's `Loadout`.
- `UAbilityLoadoutManager` stays a **constructor default subobject on the PlayerController**, never
  spawned at runtime.
- Shared state that must not desync goes on the **character** (with an `OnRep`), never on a
  PC-owned component.
- Invisibility travels through `bInvisible` + `OnRep_Invisible`. Do not go back to
  `Mesh->SetVisibility` from the ability.
- `MaxWalkSpeed` baseline stays **500** in the character constructor.
- The rule panel stays gated on `HasAuthority() && IsLocalPlayerController()`.
- **The intent model only ever proposes tokens and buckets.** `ULoadoutValidator` stays the only
  writer of a loadout, and `ParseRuleText` stays in the build as the fallback. Do not let a model
  response reach `Loadout` without passing validation.

---

# PART 9 — Glossary

**Authority** — permission to decide. `HasAuthority()` is the guard.
**Bucket** — a symbolic parameter change (`higher`, `max`) that C++ converts to a number.
**Bunch** — the packet payload carrying replication data or an RPC.
**Catalogue** — the exported token table; the intent model's entire vocabulary.
**Confidence floor** — below this score the model service returns an empty list, meaning "fall
back to the keyword parser".
**CDO** — Class Default Object; the template instance settings are read from. Why ini changes need a
restart for some classes.
**Connection** — server-side representation of one client; `IpConnection_1` in this rig.
**`DOREPLIFETIME`** — registers a property for replication.
**Derived state** — computed identically on each machine instead of being sent.
**`_Implementation`** — the body of a network function.
**Listen server** — a player that is also the server.
**NetDriver** — the thing that actually moves bytes and decides what each connection gets.
**`OnRep`** — callback after a replicated value arrives.
**Relevancy** — whether an actor is sent to a given connection at all.
**RPC** — one-shot remote function call. Server / Client / NetMulticast.
**`ScriptExecutionInEditor`** — call space meaning "run locally, don't network". The P4 villain.
**Presence head** — the part of the intent model that scores candidate tokens against the phrase,
with the candidate set in view so complements are expressible.
**SimulatedProxy** — a client's copy of an actor it doesn't own.
**Trigram fallback** — every word also enters the model as character trigrams, so an ability name
it never trained on is not `<unk>`.
**UBT** — UnrealBuildTool, the real compiler driver.

---

# PART 10 — Rolling session log

Appended after each working session, newest last. This section is the diary; the parts above are the
tutorial and change only when a fact changes.

The intent-model chapters follow this diary as **PART 11**, because that work arrived after the
diary began. The teaching version of that material — environment, corpus generation, architecture,
why buckets instead of numbers, and how to reproduce every figure — is its own document:
**`Docs/IntentModelTutorial.md`**. PART 11 is the measurement record behind it.

### Session — journal rebuilt from the raw transcript

- Source the user's full chat history was supplied, closing the gaps the compressed record left.
- Read at artefact level (not from memory): `WishCraft.uproject`, both `Target.cs` files,
  `WishCraft.Build.cs`, `Config/DefaultEngine.ini`, `Config/DefaultEditorPerProjectUserSettings.ini`,
  `AbilityTypes.h`, `AbilityComponentBase.h`, `AbilityLoadoutManager.h/.cpp`,
  `WishCraftCharacter.h/.cpp`, `WishCraftPlayerController.h/.cpp`, `WishCraftGameMode.cpp`,
  `HealthComponent.cpp`, `AbilityProjectile.cpp`, `Ability_Invisibility.cpp`,
  `Ability_SpeedBoost.cpp`, `RuleParserLibrary.cpp`, `LoadoutValidator.cpp`.
- Rewrote the document from a bug-log into a tutorial: setup layer (uproject → Target.cs → Build.cs →
  config → build tooling), a networking primer, then system-by-system with exact calls, then the
  bug hunt, then interview preparation.
- Corrections made against the earlier draft: the PIE ini precedence trap is now a numbered section
  with the actual precedence rule; the four build-time bugs from the crashed Live Coding pass are
  recorded; `MaxWalkSpeed` not replicating is documented as a first-class networking fact rather than
  a footnote; the `Build.cs`/`uproject` split (and the template leftovers) is documented as the user
  asked.
- No source changed this session. Nothing built, nothing run, project untouched.

### Session — P9: the client had no abilities

- User reported abilities working on the server but not on the client. Log confirmed it precisely: the
  host applied `fireballs and invisibility only` and only `_C_0 (authority: yes)` derived components;
  every later keypress logged `No ability equipped for that category`.
- Traced it to `UAbilityLoadoutManager::ServerSetLoadout` writing only the calling controller's
  `Loadout`. This was a design gap, not a networking failure — the fan-out to other players had never
  been written.
- Fixed in two body-only edits so Live Coding could carry them:
  `Abilities/AbilityLoadoutManager.cpp` (fan the validated specs out to every other controller) and
  `WishCraftPlayerController.cpp` (`OnPossess` inherits the match ruleset for a late joiner).
  Deliberately **no** new members and **no** new virtual functions, because either would have forced a
  full rebuild, and a rebuild closes the editor.
- Live Coding: `Compile Status: Succeeded` / `Patching Status: Succeeded`, only the pre-existing
  `C4263`/`C4264` pair on `UAbilityComponentBase::Activate`.
- Verified live in 2-client PIE by reading replicated state on both worlds: server pc1 went from `[]`
  to `['Fireball', 'Invisibility']`, the client world's controller received the same list on the next
  network tick, and both server-side characters now derive both ability components.
- Journal updated with P9 above (symptom → cause → fix → both tradeoffs → measured evidence), plus the
  three Python API facts that cost two failed verification attempts: `Exec` functions are not
  Python-exposed, `FString&` out-params are dropped from Python signatures, and `Pawn` on a
  PlayerController is a protected property.
- Still open from before, unchanged: per-token VFX (`BP_OnAbilityEffect` has no implementers), the
  `Activate`/`Deactivate` rename, late-join cosmetics, and the LLM layer (still the deterministic stub
  in `Rules/RuleParserLibrary.h`, with no HTTP/Json modules in `WishCraft.Build.cs`).

### Session — P10: health bars over every player's head

**What was asked.** A widget above each character so the host can watch everyone's health during a match.

**The one idea to take away.** There are two separate problems in a floating health bar, and mixing them
up is what makes them confusing:

1. *Getting the number to the machine.* That is replication, and it was already solved.
2. *Drawing the number attached to a body in the world.* That is a Widget Component, and it is local
   cosmetic — nothing about it is networked.

**Problem 1 — the number, and why no new code was needed.**
`Source/WishCraft/Abilities/HealthComponent.h` already declared:

- `float Health`, marked `ReplicatedUsing = OnRep_Health`
- `FOnHealthChanged OnHealthChanged` — a dynamic multicast delegate with two floats, `(NewHealth, MaxHealth)`
- `GetLifetimeReplicatedProps` registering that float in `HealthComponent.cpp`

Because the component lives on the character and the character is replicated, the server's `Health` value
is copied to every client that can see that character. `OnRep_Health` runs on each client the moment the
copied value arrives, and it broadcasts `OnHealthChanged`. So the delegate is a per-machine event stream of
"this pawn's health changed". Nothing needed adding to C++: no RPC, no new replicate, no widget code. This
is the pattern to remember — **replicate the value, and let each machine react to it locally.**

**Problem 2 — attaching a widget to a moving body.** A plain UMG widget lives in the viewport and knows
nothing about the 3D world. `UWidgetComponent` is the bridge: it is a normal actor component (so it
inherits the character's transform) that owns a User Widget instance.

- `Space = Screen` means the widget is drawn by Slate at the component's projected screen position, not as
a plane in the world. It stays a fixed pixel size at any distance and always faces the camera, which is
what a nameplate wants. (`Space = World` would draw it as a 3D quad that shrinks with distance.)
- `bDrawAtDesiredSize = True` makes it use the widget's own computed size instead of the component's
`DrawSize`. This is why the widget's root is a **Size Box** with `WidthOverride = 170`, `HeightOverride = 24`:
a root Canvas Panel would report a meaningless desired size, and the bar would come out the wrong shape.
- `Pivot = (0.5, 1.0)` puts the widget's **bottom centre** on the component origin, so it sits *above* the
point rather than centred on it. `RelativeLocation.Z = 120` is the height above the capsule centre.
- `BlendMode = Transparent` matters: the default is `Masked`, which alpha-tests, and the bar's soft 0.22-alpha
outline would be discarded.

**Why the wiring order is safe.** The character's Event BeginPlay reads
`HealthWidgetComponent -> Get User Widget Object` and must not get a null. That is guaranteed by engine
call order, not by luck: `AActor::BeginPlay()` in `Actor.cpp` loops the components calling
`Component->BeginPlay()` **first**, and `UWidgetComponent::BeginPlay()` is what calls `InitWidget()` to
create the widget. Only afterwards does the actor dispatch its own Blueprint BeginPlay. Hence no Delay and
no IsValid gate — verified in the engine source rather than assumed.

**What was built.**

| Asset | Change |
|---|---|
| `/Game/Sandbox/UI/WBP_HealthBar` | New. Size Box (170x24) → Border (dark rounded plate, 2px padding, outline) → ProgressBar `HealthFill`. |
| `/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter` | New `HealthWidgetComponent` (Widget Component) using that widget. |
| `WBP_HealthBar` graph | `InitHealth(Health)` sets the bar from `GetHealth/GetMaxHealth` then binds `OnHealthChanged` to a custom event `HandleHealthChanged(NewHealth, MaxHealth)` which re-sets the bar. |
| `BP_ThirdPersonCharacter` graph | `Event BeginPlay → GetUserWidgetObject → Cast to WBP_HealthBar → InitHealth(HealthComponent)`. |

**Both halves are needed and neither is optional.** `InitHealth` sets the bar **and** binds the delegate: the
initial set is what makes a bar correct for a player who joined before any damage happened (their
`OnHealthChanged` already fired during `HealthComponent::BeginPlay`, before any widget existed to hear it);
the bind is what keeps it current afterwards. Getting only the bind means a full-health bar never appears;
getting only the initial set means the bar freezes at whatever health it had when the pawn spawned.

**Two facts worth defending in an interview.**

- A floating health bar in a multiplayer game normally costs **zero** network traffic of its own. The pawn,
the health float, and their replication were already there; the widget is rendered locally on each client
from data it already receives.
- Screen-space widget components do **not** replicate. Each client constructs its own widget instance
from the class set on the component template. The component is a default sub-object of the character
Blueprint, so every client gets one for free when the pawn arrives.

**Tradeoffs taken.**

- The bar shows for the local player too, floating over their own character in third person. Hiding it from
the owner would need `bOwnerNoSee`-style filtering, and `bOnlyOwnerSee`/`bOwnerNoSee` do not apply to
screen-space widgets, so it would have to be done in the widget's own Construct. Left visible deliberately.
- `InitHealth` divides by `MaxHealth` unguarded. `MaxHealth` defaults to 100 and nothing sets it to 0, so a
guard would be dead code today. If a future ability modifies `MaxHealth`, add the guard there.

**Not yet done.** No PIE verification of this session's change: the running PIE session's pawns were spawned
before the component existed, so the bars only appear for pawns spawned after a Play-In-Editor restart.

**Process note that cost time.** The first `edit_widget` attempt failed because
`bUseBrushTransparency` was written on the brush instead of inside `OutlineSettings`, and the error message
`unknown field ... in Border.Background` listed the legal fields. `edit_blueprint` rejected a call that had
`components` but no `properties` key. Both are schema facts, not design errors: read the error, fix the
field, retry. The failed add left the widget tree **empty**, so nothing had to be cleaned up — but a
partially-applied add is possible and the tree should be read before retrying.

### Session — P10a: the "Old World not cleaned up by GC" ensure

**Symptom.** Every Play-In-Editor start logged three errors of the form:

```
Ensure condition failed: false
[ReferenceChainSearch.cpp] [Line: 2027]
Old World /Engine/Transient.World_2 not cleaned up by GC!
Object REINST_WBP_HealthBar_C_1 ... is being referenced by TransBuffer ...
```

The health bars themselves worked perfectly throughout.

**Read the message as a reference chain, top to bottom.** It is a garbage-collection post-mortem, not a
failure in game code. Each line answers "who is holding this object alive?":

| Line | Meaning |
|---|---|
| `Old World ... not cleaned up by GC` | a throwaway world survived collection when a PIE session started |
| `Object ... is being referenced by TransBuffer` | the holder is the editor's **undo/redo buffer** |
| `TransBuffer -> AddReferencedObjects(...)` | the undo system itself is rooting that object |
| `-> World /Engine/Transient.World_2` | and that object's outer is the transient world |

The `ensure` fires in `ReferenceChainSearch.cpp` only after the reference search has proved a leak, which is
why it prints the whole chain: it is telling you exactly what to delete or unroot.

**Cause — and it was Aura's own doing, not the game's.** Rendering a Widget Blueprint for a visual check
(`render_widget`) creates a live *instance* of it inside a throwaway `/Engine/Transient.World_N`. Applying
per-state property overrides to that instance goes through the editor transaction system, so the undo buffer
recorded the instance. Then the widget Blueprint was **recompiled**, and recompiling reinstances live
objects — hence the `REINST_` prefix on the name. The undo buffer still held the old, reinstanced copy, and
through it, its transient world.

**Evidence that pins it down.**

- Three worlds pinned (World_2, World_4, World_5) — the three render worlds created by the two
  `render_widget` calls made during this session.
- The instance name in the error, `WBP_HealthBar_C_1`, is exactly the instance name the render tool reported
  back in its geometry output.
- The first occurrence is timestamped one second after those renders, and it repeats identically on each
  later PIE start with the same three worlds — bounded, not growing.
- No C++ in the project touches widgets in a transient world. Nothing in `Source/WishCraft` is involved.

**Fix.** Restart the editor. `TransBuffer` and the transient worlds are process-lifetime objects, so a
restart is the clean fix, and nothing is lost because both blueprints are already saved and compiled. There
is no exposed "clear undo history" command in 5.8's `UnrealEd` source (searched for `ClearUndoHistory`,
`DiscardHistory`, `Trans->Reset()` — no matches), so there is nothing to call instead. Leaving it is also
fine: it is three small transient worlds and three log lines per PIE start, with zero runtime cost and no
effect on packaging.

**Lesson to keep.** Editor-side verification tools can create editor-state debris that later surfaces as an
error in an unrelated system. When an ensure names a mechanism (`TransBuffer`) rather than a gameplay
system, look at what the *editor* did, not at the game.

**Separate real bug found in the same log — replicated T Maps silently do nothing.**
The log also carried `LogProperty: Error: Replicated TMaps are not supported.` twice, on the same frame the
ruleset replicated. That message comes from `FMapProperty::NetSerializeItem` in `PropertyMap.cpp`, whose
entire body is that log line followed by `return 1` — it reports success and serialises **nothing**.

`Abilities/AbilityTypes.h` declares `FAbilitySpec::Params` as a `TMap<FName, float>`, and
`AbilityLoadoutManager.h` replicates `TArray<FAbilitySpec> Loadout`. So the token list replicates but every
spec's `Params` map arrives **empty on clients** — the two log lines being the two equipped specs, Fireball
and Invisibility. `RefreshAbilityComponents` then hands that empty map to
`ConfigureAbility(Spec.Token, *Definition, Spec.Params, ...)`, so on a client every `GetParam` returns its
fallback while the server uses the real tuned value.

Why nothing visibly breaks today: damage and ability duration are decided on the server, and invisibility
travels as the replicated `bInvisible` bool. The divergence would show up the moment a client-side
calculation depended on a rule-tuned parameter — a cooldown length read locally, or a VFX parameter.

**Rule to remember:** a replicated struct may contain `int`, `float`, `bool`, `FName`, `FString`, nested
structs and arrays — but not `TMap` or `TSet`. If a map has to cross the wire, replicate it as an
`TArray` of key/value structs and rebuild the map on the receiving side. Changing this needs a real
rebuild (the struct's layout changes), so it is deliberately not being done during a Live-Coding session.

### Session — P10b: making the ability parameters actually replicate

The fix promised in P10a, applied and **verified in a two-client PIE session** — a `USTRUCT` layout
change cannot be patched by Live Coding, and the docked Aura panel cannot drive a rebuild because closing
the editor closes the panel, so the user rebuilt from the launcher.

**The shape of the fix.** A replicated struct cannot carry a map, so the map had to go — not be hidden.
Marking the `TMap` `NotReplicated` would have stopped the log line while still sending nothing, which is
worse than the bug: a silent failure instead of a loud one. So the replicated representation became an
array of pairs, and the map survives only on the non-replicated side where it is genuinely convenient.

New in `Abilities/AbilityTypes.h`:

| Type | Role |
|---|---|
| `FAbilityParam` | `FName ParamName` + `float Value`. The replicated unit. |
| `FAbilitySpec::Params` | now `TArray<FAbilityParam>` — replicates |
| `FAbilitySpec::GetParam(Name, Fallback)` | linear lookup; N here is 2–3 parameters per token |
| `FAbilitySpec::SetParam(Name, Value)` | add-or-overwrite, used by the defaults builder and the validator |
| `FAbilitySpec::ToParamMap()` | array → `TMap` for the ability components, which are **not** replicated |

**Call sites changed, five files total.**

| File | Change |
|---|---|
| `Abilities/AbilityTypes.h` | new struct, `Params` retyped, three helpers |
| `Abilities/AbilityTokenTable.cpp` | `BuildDefaultSpec` copies `DefaultParams` into the array via `SetParam` |
| `Abilities/LoadoutValidator.cpp` | `FindOrAdd` became `GetParam` → clamp → `SetParam`; the clamp-and-log behaviour is unchanged |
| `Abilities/AbilityLoadoutManager.cpp` | cooldown lookup uses `GetParam`; `ConfigureAbility` receives `Spec.ToParamMap()` |
| `Abilities/AbilityComponentBase.cpp` | untouched — it still takes a `TMap`, which is correct because that component is not replicated |

**Why an array of structs and not two parallel arrays.** A second replicated `TArray<float>` would have kept
the existing `TMap` intact, but two arrays can drift out of order and nothing would catch it. One array of
`FAbilityParam` keeps each name welded to its value, so a reordering bug is impossible by construction.

**Two deliberate design notes.**

- `GetParam` is a linear scan. With two or three parameters per token that is faster than hashing, and it
  avoids storing two representations of the same data.
- `ToParamMap()` returns by value and is called once per component creation, not per frame. Correctness over
  micro-optimisation; the map is small and short-lived.

**No Blueprint fallout.** Checked `WBP_RuleInput`'s EventGraph before committing to the type change: it only
calls `ApplyRuleText` and reads the returned error string. Nothing in any graph reads `Loadout` or a spec's
`Params`, so retyping the member cannot break a node pin.

**Build.** Rebuilt from the launcher. Confirmed live by loading `/Script/WishCraft.AbilityParam` in the
running editor — it resolves, which proves the new reflection data is in the loaded module. The log now
contains zero occurrences of `Replicated TMaps are not supported`.

**The measurement that matters.** The host applied `fireballs and invisibility only`, then the *client*
world was read on a later call so a network tick had passed:

| Where | Fireball | Invisibility |
|---|---|---|
| Server PC 0 | `Damage 20, ProjectileSpeed 2500, Cooldown 1.5` | `Duration 5, Cooldown 8` |
| **Client PC** | **`Damage 20, ProjectileSpeed 2500, Cooldown 1.5`** | **`Duration 5, Cooldown 8`** |

Identical on both sides. Before the change the client's `Params` array was empty for every spec — the two
log lines in P10a were exactly these two specs failing to serialise.

Note the method: the rule was applied in one script and the client was read in a **separate** call. Reading
replication inside the script that causes it always returns stale data, because a blocking Python call never
lets the network tick. That is the same trap that produced a false `[]` during the P9 verification.

**One lesson about the build itself.** A reflected struct's member list, `GetLifetimeReplicatedProps` layout
and generated code all change here, which is why Live Coding could not carry it and the editor had to close.
Function-body changes go through Live Coding; struct and class layout changes never do.

---

### Session — P10c: moving the ruleset to the GameState

P9 made the rules reach the client, but it did it the hard way. This session replaces the mechanism with the
one Unreal actually provides, and in doing so removes a whole category of bug.

**The smell.** The rules lived in `UAbilityLoadoutManager::Loadout`, which is a component *on each player's
PlayerController*. That meant every controller owned its own replicated copy, and the server had to walk the
player list handing the result out by hand:

```cpp
for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
{
    OtherManager->Loadout = Loadout;   // P10c deleted this loop
    OtherManager->States.Reset();
    OtherManager->RefreshAbilityComponents();
}
```

Three things go wrong with a manual fan-out like that, and all three are structural rather than sloppy:

| Problem | Why it happens |
|---|---|
| It has to be found and edited every time a new consumer appears | The distribution logic is copy-pasted in a function, not a property of the data |
| A player who joins *later* is missed entirely | There is no record of "what the rules are", only per-player copies — so `OnPossess` had to scan sibling controllers for a non-empty one |
| The copies can disagree | Two `TArray` copies with no single owner means any partial update leaves them out of sync |

All three disappear the moment the data has **one owner that every machine already knows how to listen to**.

**The rule to memorise.** Replication is not something you call; it is a property of an actor that exists on
the server and is relevant to a client. Ask "which actor is replicated *to* everyone?" and put the data
there:

| Actor | Server | Client | Can it carry match-wide state? |
|---|---|---|---|
| `AGameModeBase` | yes | **does not exist** | No — nothing to replicate *from* on a client |
| `AGameStateBase` | yes | yes (always relevant) | **Yes** |
| `APlayerController` | yes | only its own | No — reaches exactly one connection |
| `APawn` | yes | yes | Yes, but it is per-body, not per-match |

The `APlayerController` row is the one that caused P9's original bug: a PlayerController's RPCs only reach the
connection that owns it, which is why the cosmetic had to be routed through the pawn. Same root cause here.

**What changed. Four files.**

| File | Change |
|---|---|
| `WishCraftGameState.h/.cpp` | **new.** `AWishCraftGameState : AGameStateBase` owning `UPROPERTY(ReplicatedUsing = OnRep_Ruleset) TArray<FAbilitySpec> Ruleset`, plus `ServerSetRuleset()` and `BroadcastRulesetChanged()` |
| `Abilities/AbilityLoadoutManager.h` | `Loadout` lost `ReplicatedUsing`; `OnRep_Loadout` deleted; `ApplyRuleset()` and `PullRulesetFromGameState()` added |
| `Abilities/AbilityLoadoutManager.cpp` | `DOREPLIFETIME` for `Loadout` removed; `ServerSetLoadout` now publishes to the GameState instead of fanning out |
| `WishCraftPlayerController.cpp` | `OnPossess`'s sibling-scan replaced with one `PullRulesetFromGameState()` |
| `WishCraftGameMode.cpp/.h` | `PreInitializeComponents()` override pins `GameStateClass` |

**The data flow now.** One replicated array, three separate moments that read it:

```
Server: host types rules
  -> UAbilityLoadoutManager::ServerSetLoadout()          validate
  -> AWishCraftGameState::ServerSetRuleset()             assign Ruleset
  -> OnRep_Ruleset()                                     called MANUALLY - see below
  -> BroadcastRulesetChanged()
       -> every PC's Manager->ApplyRuleset(Ruleset)      server + all clients

Client: Ruleset arrives over the network
  -> OnRep_Ruleset() (called by the engine this time)
  -> BroadcastRulesetChanged()
       -> the local PC's Manager->ApplyRuleset(Ruleset)
```

`ApplyRuleset` is the single place that turns "a ruleset" into "this player's abilities" — it copies into the
local `Loadout`, rebuilds `States` on the server only, then calls `RefreshAbilityComponents()`.

**Why the server calls its own `OnRep` by hand.** A `ReplicatedUsing` callback fires on *receivers*. The server
is the sender, so it never receives its own update and `OnRep_Ruleset` would never run there — the host would
keep its old abilities while everyone else updated. Calling it explicitly after the assignment is the standard
fix. It is safe to call twice because the function is idempotent: it re-derives from current state.

**Why `States` stayed on the manager.** `Ruleset` is match-wide and identical for everyone. `States` is the
opposite — each player has their own cooldowns and their own active flags. So the split is by *ownership*, not
by convenience:

| Data | Owner | Replicated? |
|---|---|---|
| Which tokens are equipped this match, with params | `AWishCraftGameState` | yes, once, to everyone |
| That player's cooldown timestamps and active flags | `UAbilityLoadoutManager` (per player) | yes, to the owner |
| The derived ability *components* | nobody — rebuilt locally from the above | no |

The third row is the important one. Ability components are never replicated. Both sides build them from the
same data, which is why `RefreshAbilityComponents()` runs on the server and on every client.

**The trap: a Blueprint beat the constructor.** `BP_ThirdPersonGameMode` explicitly sets `GameStateClass` to
plain `GameStateBase`, and a Blueprint property value wins over a C++ constructor default. So setting it in
`AWishCraftGameMode()` would have silently done nothing, and `Cast<AWishCraftGameState>` would have returned
null forever.

The engine source settles where the fix has to go. `AGameModeBase::PreInitializeComponents()`
(`Engine/Source/Runtime/Engine/Private/GameModeBase.cpp:116-144`) is what reads the property and spawns the
GameState:

```cpp
if (GameStateClass == nullptr) { GameStateClass = AGameStateBase::StaticClass(); }
GameState = World->SpawnActor<AGameStateBase>(GameStateClass, SpawnInfo);
```

And `InitGame` is called from `UWorld::SetGameMode` *after* the GameMode actor is initialised
(`World.cpp:6031-6035`), i.e. **after** that spawn. So `InitGame` is too late; `PreInitializeComponents` is the
one hook that runs after the Blueprint has applied its values but before the property is read:

```cpp
void AWishCraftGameMode::PreInitializeComponents()
{
    GameStateClass = AWishCraftGameState::StaticClass();   // set BEFORE Super
    Super::PreInitializeComponents();
}
```

Setting it before `Super` is the whole trick, and this is also why the engine's own code assigns
`GameStateClass` at runtime rather than relying on the config default.

**Three pull points, on purpose.** A client can receive `Ruleset` before its PlayerController exists, in which
case `BroadcastRulesetChanged` had nobody to notify. So the manager also pulls:

| Moment | Covers |
|---|---|
| `OnRep_Ruleset` → broadcast | the live case: rules changed mid-match |
| `OnPossess` → `PullRulesetFromGameState()` | a player who joins after the rules were set |
| `BeginPlay` → `PullRulesetFromGameState()` | the component existing before possession |

`PullRulesetFromGameState` deliberately uses a plain `Cast<AWishCraftGameState>` rather than the
`GetGameState<T>()` template, and logs a warning when the cast fails. The template returns null on a mismatch,
which is precisely the silent failure that would have hidden the Blueprint override above.

**Build.** Adding a `UCLASS` changes the reflection data, so this cannot go through Live Coding — the module
needs a full rebuild with the editor closed, then the editor reopened.

**How to tell it worked.** In the log, after a rebuild:

| Line | Meaning |
|---|---|
| `Ruleset applied on <PC> (2 token(s), authority: yes)` | the host's manager applied the rules |
| `Ruleset applied on <PC> (2 token(s), authority: no)` | the **client's** manager applied them — this is the one P9's bug would have missed |
| `Ruleset unavailable: this level's GameState is not AWishCraftGameState` | `GameStateClass` is still not pinned; the refactor is inert |

**Measured, in a two-client PIE session.** Rebuilt, then verified in three steps. All three passed.

First, the class itself, without starting a session:

| Check | Result |
|---|---|
| `load_class("/Script/WishCraft.WishCraftGameState")` | resolves |
| CDO `bReplicates` / `bAlwaysRelevant` | True / True |
| CDO `Ruleset` | present, 0 entries |

Second, the Blueprint override trap, which was the real risk. `BP_ThirdPersonGameMode` still declares
`GameStateClass = GameStateBase` in its defaults, so this is the question that mattered — and both worlds came
back with our class, meaning `PreInitializeComponents` overrode the asset at runtime:

```
world 0 (server): GameState=WishCraftGameState ruleset=0
world 1 (client): GameState=WishCraftGameState ruleset=0
```

Third, the rules themselves. Host applied `fireballs and invisibility only`, and the log recorded the whole
path — note the frame numbers, `[492]` on the server and `[493]` on the client:

```
[492] Ruleset replicated: Fireball, Invisibility
[492] Ruleset applied on BP_ThirdPersonPlayerController_C_0 (2 token(s), authority: yes)
[492] Ruleset applied on BP_ThirdPersonPlayerController_C_1 (2 token(s), authority: yes)
[492] Rules applied: Fireball, Invisibility
[493] Ruleset replicated: Fireball, Invisibility
[493] Ruleset applied on BP_ThirdPersonPlayerController_C_0 (2 token(s), authority: no)
```

Read that carefully, because each line is evidence for a different claim:

| Line | What it proves |
|---|---|
| `[492] Ruleset replicated` | the server ran `OnRep_Ruleset` **manually** — without that call the host would never update |
| `[492] ..._C_0` and `..._C_1`, both `authority: yes` | one assignment reached **both** server-side controllers. Three lines of `BroadcastRulesetChanged` replaced the old per-controller push loop |
| `[493] Ruleset applied ... authority: no` | a client genuinely received it over the wire and re-derived locally |

And `Ruleset unavailable` never appears — zero occurrences — so `GameStateClass` was pinned successfully.

The final state, read back from both worlds. Client and server are identical, which is the property the whole
refactor was for:

| Where | GameState ruleset | Fireball | Invisibility |
|---|---|---|---|
| Server | `[Fireball, Invisibility]` | `Damage 20, ProjectileSpeed 2500, Cooldown 1.5` | `Duration 5, Cooldown 8` |
| **Client** | **`[Fireball, Invisibility]`** | **`Damage 20, ProjectileSpeed 2500, Cooldown 1.5`** | **`Duration 5, Cooldown 8`** |

**The tutorial point worth keeping.** P9 made this work by pushing copies around by hand, and it worked — but
only for the cases one person remembered to handle. P10c replaced the mechanism with a property declaration,
and the class of bug disappeared rather than being fixed. When you find yourself writing a loop over
`GetPlayerControllerIterator()` to keep several copies in sync, the loop is the smell: a replicated owner is
usually available, and it is usually `AGameStateBase`.

---

### Session — P10d: renaming `Activate`/`Deactivate` to `BeginEffect`/`EndEffect`

The open item from P7. Small change, but it closes a genuine hazard rather than a cosmetic one.

**Two collisions, and only one of them was loud.** `UAbilityComponentBase` inherited from
`UActorComponent`, which already has these members:

| Base member | Signature | What it does |
|---|---|---|
| `UActorComponent::Activate(bool)` | `void(bool)` | component-level: lets the component tick |
| `UActorComponent::Deactivate()` | `void()` | component-level: stops it ticking |

The ability base then declared `virtual float Activate();` and `virtual void Deactivate();`. The difference
between the two is the whole lesson:

- **`Activate()` returning `float` does not match `void(bool)`**, so it did not override — it *hid* the base
  member and the compiler said so: `C4263` (does not override any base virtual) and `C4264` (no override
  available). Noisy, harmless.
- **`Deactivate()` matched `void()` exactly**, so it *silently overrode* the component-lifecycle function.
  No warning at all. Anything in the engine that deactivated the component called the ability's version
  instead — and worse, the intent was reversed: the engine thought it was shutting a component down while the
  ability thought it was ending an effect.

That asymmetry is the rule to carry: **a matching signature is silent, a mismatched one is loud.** Silence is
the dangerous outcome, because it means the compiler believes you meant it.

**The fix.** Rename so the ability API shares no word with the component lifecycle, and spell every override
`override` so the compiler keeps checking:

| Before | After | Where |
|---|---|---|
| `virtual float Activate()` | `virtual float BeginEffect()` | `AbilityComponentBase.h` |
| `virtual void Deactivate()` | `virtual void EndEffect()` | `AbilityComponentBase.h` |
| `UAbilityComponentBase::Activate()` | `BeginEffect()` | `AbilityComponentBase.cpp` |
| `UAbilityComponentBase::Deactivate()` | `EndEffect()` | `AbilityComponentBase.cpp` |
| `UAbility_Fireball::Activate` | `BeginEffect` | `Ability_Fireball.h/.cpp` |
| `UAbility_Invisibility::Activate` | `BeginEffect` | `Ability_Invisibility.h/.cpp` |
| `UAbility_SpeedBoost::Activate` | `BeginEffect` | `Ability_SpeedBoost.h/.cpp` |
| `Component->Activate()` | `Component->BeginEffect()` | `AbilityLoadoutManager.cpp:379` |
| `Component->Deactivate()` | `Component->EndEffect()` | `AbilityLoadoutManager.cpp:401` |

Only `Activate` was actually overridden by the three subclasses — no subclass implements `Deactivate`, so
`EndEffect` exists once in the base and is called once by the manager. Finding that out first is what kept
this to a mechanical rename.

**`CanActivate()` was deliberately left alone.** It collides with nothing in `UActorComponent`, it is a pure
gate, and "can activate" still reads correctly in English even next to `BeginEffect`. Renaming members that
are not broken would just churn the diff.

**Why this compiled through Live Coding and P10c did not.** These are ordinary C++ virtuals, not `UFUNCTION`s,
so nothing in the reflection data changed — no `UCLASS` layout, no generated code, no `DOREPLIFETIME` list. Live
Coding only has to patch function bodies and symbol tables. P10c added a `UCLASS`, which is reflection data,
and that is what forced a closed-editor rebuild. The rule from P10b holds and now has a second data point:
**new or reshaped reflected types need a full rebuild; everything else does not.**

**Checked before and after.** A module-wide grep for `::Activate()`, `->Activate()`, `::Deactivate()` and
`->Deactivate()` inside `Source/WishCraft/Abilities` returns nothing, and the same grep for
`BeginEffect|EndEffect` returns every declaration, definition, override and call site exactly once. Nothing in
`Variant_Combat` is affected — `ActivateInteraction`/`DeactivateInteraction` are the engine template's own
unrelated API on a separate interface.

**Runtime proof, in a two-client PIE session.** A rename of a `virtual` is exactly the kind of change static
grep cannot fully certify: the symbol can be right everywhere and still fail to dispatch if a vtable slot is
wrong. So the test was to make an ability actually run and read what it logged. The log text itself changed in
the rename (`"activated"` → `"began"`), which makes it a free marker for "the new code ran":

```
[554] Ability Fireball began                       <- base BeginEffect, reached via Super::BeginEffect()
[554] Fireball cast by BP_ThirdPersonCharacter_C_0 <- the override body continued past Super
[554] Ability Invisibility began
[719] Ability Invisibility ended                   <- 4.95s later, matching Duration 5.0
[719] Ability effect off Invisibility for BP_ThirdPersonCharacter_C_1 (client)
```

| Line | What it proves |
|---|---|
| `Ability Fireball began` | `UAbility_Fireball::BeginEffect` dispatched and called the base — the virtual works under its new name |
| `Fireball cast by ...` | the override ran *past* `Super`, so this is the subclass body and not the base fallback |
| `Ability Invisibility ended` at frame 719 | `EndEffect` was reached through the timed path: `BeginEffect` returned 5.0 → timer → `EndActiveAbility` → `EndEffect` |
| `Ability effect off Invisibility ... _C_1 (client)` | the cosmetic still reaches the non-owning client, so the rename did not disturb the P9 fix |

And the client still builds its own components from the replicated ruleset, unchanged by the rename:

```
client tokens: ['Fireball', 'Invisibility']
client components: Ability_Fireball / Ability_Invisibility
```

**One false alarm worth recording, because it teaches the method.** A first grep for `Fireball cast` inside a
25-line window around `Ability` returned nothing, which looked like the projectile had stopped spawning. Widening
the query found it at the same millisecond as `Ability Fireball began`. The lesson is the same one P9 taught in
a different costume: **a windowed log query proves what is in the window, not what is absent from the file.**
Before reporting a missing log line as a regression, grep for that line directly.

---

### Session — P10e: per-token VFX, driven from the token table

The last item that had been sitting open since P7: `BP_OnAbilityEffect` had zero implementers, so abilities changed
the game state and nothing on screen except the projectile.

**The gap was never the plumbing.** `MulticastAbilityEffect_Implementation` already ran on the server and on every
client that can see the character, and already called two hooks. Both were empty:

| Hook | Reaches | Was implemented? |
|---|---|---|
| `Component->HandleCosmetic(bStarting)` | server + the character's **owner** only | no — `ComponentClass` points at native `Ability_*` classes, so no BP subclass exists |
| `BP_OnAbilityEffect(Token, bStarting)` | **every** machine that can see the character | no — `BP_ThirdPersonCharacter` has no such event |

**Why the effect data went on the token row.** The interesting half of that table is the first row: an effect
implemented on the component would have been invisible to the other player, because a machine that does not own a
character has no ability component at all (the loadout only replicates to its owner — the P9 lesson, arriving
again). So the VFX lives where it can be read by anyone, and the reading happens in the multicast:

```
FAbilityTokenDefinition                      (DA_AbilityTokens, one row per token)
  StartEffect / SustainEffect / EndEffect    <- Niagara assets, each optional
  AttachSocket (default hand_r), EffectOffset
        |
        v  read at runtime by
AWishCraftCharacter::PlayTokenEffect(Token, bStarting)
        ^  called from
MulticastAbilityEffect_Implementation   -> server + every client that can see the character
```

`PlayTokenEffect` uses `UNiagaraFunctionLibrary::SpawnSystemAttached` on `GetMesh()`. The one-shots are spawned
with auto-destroy on and need no bookkeeping. The **sustain** is spawned with auto-destroy **off** and kept in
`TMap<FName, TObjectPtr<UNiagaraComponent>> TokenEffects`, because a looping system would otherwise outlive the
ability that owns it — deactivated and destroyed on the stop half, before the end effect fires.

**The static table accessor, and why a property would not do.** `UAbilityLoadoutManager::TokenTable` is a property
on a component that only exists where a player owns the character. The machines that need to render *someone
else's* effect are precisely the ones without it. So `UAbilityTokenTable::GetDefaultTable()` caches a
`TWeakObjectPtr` around one `LoadObject`, and the manager's `BeginPlay` fallback now calls it too — the asset path
is spelled in exactly one place. Weak, not strong, so the table stays collectable; a stale pointer just reloads.

**The row decides the shape, and one row is deliberately empty.**

| Token | StartEffect | SustainEffect | EndEffect | Socket |
|---|---|---|---|---|
| Fireball | `NS_Ability_FireballCast` | — (the projectile is the sustained half, and it already replicates) | — | `hand_r` |
| Invisibility | `NS_Ability_InvisibilityPulse` | — on purpose: a looping aura would **outline a player who is supposed to be invisible** | `NS_Ability_InvisibilityPulse` | mesh origin |
| SpeedBoost | — | `NS_Ability_SpeedTrail` | — | mesh origin |

That empty cell is a design decision, not an omission, and it is the kind of thing the data-driven layout makes
explicit in one table instead of buried across three Blueprint graphs.

**Measured, by the verification harness playing the game (host side).**

| Case | Result |
|---|---|
| Fireball | `NS_Ability_FireballCast` on `BP_ThirdPersonCharacter_C_0` at socket `hand_r` for ~1.3 s, `AbilityProjectile` still spawned, log `Fireball cast by ...` and `Effect start Fireball at socket hand_r ... (server)` |
| SpeedBoost | trail attached within a tick, `MaxWalkSpeed` 500 → 800 (1.6x), at 5 s the component was **destroyed** and speed restored to 500, log `Effect sustain cleared SpeedBoost ... (server)` |
| Invisibility | mesh hidden, `NS_Ability_InvisibilityPulse` spawned at activation **and again** at expiry (two distinct components), log `Effect start` then `Effect end` |
| Not owner-only | **untested** — the harness launched a single-client Standalone world (1 PlayerController, no NetDriver, no `UEDPIE_1`), and its session launcher ignores client-count/net-mode arguments. Zero `(client)` lines exist in the log, so the non-owner path was neither shown nor falsified |

That last row is the honest gap and it is the same trap as **P4** in a new costume: a harness that cannot open the
second client is not evidence that the second client works. The distinction that matters is *untested* versus
*failed* — this run produced the former, and the code path it would have exercised is the same one already proven
in P9/P10d for `MulticastAbilityEffect` delivery.

**The build rule, again.** This needed a closed-editor rebuild, unlike the `BeginEffect` rename, because adding
`UPROPERTY`s to a `USTRUCT` changes reflection data and adding a module dependency changes the link. Second data
point for the rule from P10d: **reshaped reflected types need a full rebuild; function bodies do not.**

---

# PART 11 — The intent model

This part is the evidence: what was built, what the numbers said, and the three occasions when the
*measurement* was wrong rather than the model. If you are learning this, read
`Docs/IntentModelTutorial.md` first — it covers the environment, the corpus, the architecture and
the reproduce commands. Read this part for the measurements and the wrong turns behind them.

## P11 — The intent model: a purpose-trained parser, and what the measurements said about it

Everything above this point reads rules with substring matching. `URuleParserLibrary::ParseRuleText`
lowercases the line, strips spaces and hyphens, and checks whether each ability's name or any of its
keywords appears inside it. That is why `everything except invisibility` equips Invisibility: the parser
finds the word *invisibility* and has no concept of the word *except*. It also explains why no ability has
ever had a tunable number — the parser has no notion of one.
This part of the build is a small neural network trained on this project's own vocabulary to fix both.
It lives entirely in Python under `Tools/intent_model/` and talks to the game through one HTTP endpoint.

### The one rule that makes it safe

The model's entire output is ability **names plus bucket labels**:

```json
{"tokens": [{"name": "Fireball", "params": {"Damage": "higher", "Cooldown": "higher"}}]}
```

A bucket is a word, not a number: `much_lower`, `lower`, `default`, `higher`, `max`. `schema.bucket_to_value`
turns `higher` into `2 x default` and clamps to the range. The model never sees a min/max, never multiplies,
and cannot emit 99999. `ULoadoutValidator::ValidateTokens` on the C++ side remains the only thing that
writes a spec. This is the same division of responsibility as P5: the thing that decides *what* is separate
from the thing that decides *whether it is legal*.

### How the model is wired

Two towers share one idea. The **phrase tower** (`phrase_encoder`) reads the player's sentence. The
**text tower** (`text_encoder`) reads ability text and parameter names — the *same* encoder instance for
both, which is what puts "fireball" and "Damage" in one comparable space. Presence is then a decision
over candidates: `presence_head` scores every ability in the request's catalogue against the phrase.

One detail carries the vocabulary-agnostic claim. Every word is emitted as `[word_id, tri1, tri2, ...]`,
where the trigrams are hashed into 2,048 buckets. A word the model has never seen — `blink` — still
produces a usable vector from its `bli`/`lin`/`ink` pieces, so the held-out-ability test is a real test
rather than a guaranteed zero.

The catalogue arrives **in the request body**, not from a baked-in list. Adding a seventh ability is a
game-side data change; nothing is redeployed and no checkpoint is retrained. That is the difference
between a model and a `switch` statement.

### The hard numbers

`test.jsonl` is 1,166 rows from 8 template families held out whole, so no near-duplicate sentence leaks
across the line. The baseline is a faithful port of the shipped parser, scored on the identical rows.

| | token-set exact | intent exact | bucket accuracy |
|---|---|---|---|
| keyword baseline | **0.652** | **0.317** | 0.852 |
| intent model | 0.464 | 0.143 | 0.826 |

**The baseline wins, and the README says so at the top rather than in a footnote.**

Two things are worth saying carefully, because either one alone would be an excuse but together they are
a real distinction. First, the corpus generator builds phrases out of the catalogue's *own keyword lists*,
so the baseline is handed the word→ability mapping while the model must learn it from 7,714 examples —
the benchmark is biased toward the baseline. Second, and more importantly: **the baseline scores exactly
0.000 on every parameter family**, because it is structurally incapable of producing anything but
all-default. It cannot do the job the model exists for.

### Three measurements that changed the design

**First: the trainer was optimising itself into a corner.** Bucket accuracy fell from 0.832 to 0.654 over
8 epochs. The suspect was the inverse-frequency class weighting at `train.py`, which at exponent 0.5 was
pushing the model to guess rare buckets. Softening to 0.2 fixed the collapse — but only replaced one
wrong behaviour with another, and the next measurement is the one that mattered.

**Second: bucket accuracy with no baseline is an uninterpretable number.** Counting the truth labels showed
`test.jsonl` is **85.2% `default`**. Always answering "default" therefore scores 0.852, and the model's
0.826 was *below* it. On the only two families that require a non-default answer it scored 0.313 and 0.433
against majorities of 30.4% and 63.8% — no skill, and on one family worse than a constant. The exponent was
never the problem: at 0.2 the `default` class still holds about 74% of the loss mass, so "always default"
stays close to optimal and gradient descent finds it.

**Third: a threshold sweep cleared calibration as an explanation.** Before touching architecture, the
confidence cut-off was swept from 0.5 to 0.9. Token-set exact moved 0.394 → 0.443. A +0.05 shift against a
−0.26 gap means the signal is absent, not mis-scaled. That is the difference between tuning and rebuilding,
and it cost 30 seconds instead of a retrain.

### The architectural defect this exposed

Both `exclude_*` families scored **0.000** token-set accuracy despite exclusion families being in *training*.
The reason is structural. Presence was `sigmoid((phrase @ token.T) / temperature)` — each candidate scored
independently. But "everything except X" is a property of the **set**, not of any single pair; no per-pair
score can express it.

So the presence head was replaced with an MLP over `[phrase, candidate, candidate-set mean, similarity,
similarity − best]`, giving it the ability to rank rather than merely match. A max-pooled phrase was added
to the bucket head so a single discriminating word survives pooling.

It helped adjacent things and **failed at its main target**:

| | before | after |
|---|---|---|
| held-out `Blink` detection | 0.260 | **0.517** |
| held-out false positives | 0.129 | 0.215 |
| `param_multi_agree` presence | 0.337 | **0.642** |
| `exclude_all_but` presence | 0.000 | 0.000 |
| bucket skill on parameter families | none | none |
| test intent exact | 0.154 | 0.129 |

Detection doubled, and so did false positives — read together, the model got better at saying *yes* to an
unknown ability, not much better at saying yes to the right one. Negation is unchanged, and max-pooling did
not rescue the bucket head. Both are recorded as measured limits, not as work in progress.

### What actually ships

The **contract**, and it is verified against live uvicorn rather than assumed:

| Phrase | Response |
|---|---|
| `punchy but slow fireballs` | Fireball — `Damage higher`, `ProjectileSpeed higher`, `Cooldown higher` |
| `everything except invisibility` | `{"tokens": []}` |
| `let me vanish twice as long` | Invisibility — `Duration higher`, `Cooldown higher` |
| `make me a sandwich` | LightningStrike, all default |

The empty list is the designed failure: below the confidence floor the service returns nothing, which is
the caller's signal to fall back to the keyword parser. A confident wrong loadout is worse than no answer.

The defects are visible in the same table. `slow` produced `ProjectileSpeed higher` — the model has learned
that *a comparative is present*, not which direction it points. `Cooldown higher` appears spuriously on the
invisibility phrase. And out-of-domain nonsense produced a confident false positive instead of the empty
list. None of these is hidden behind a summary statistic.

### Verify status

| Case | Result |
|---|---|
| Beats the baseline on `golden.jsonl` | **FAILS** — model 0.450 vs baseline 0.700 token-set exact |
| Complement works in game | **FAILS at model level** (`{"tokens": []}`); in-game untested |
| Comparative lands in a bucket | model gives `Duration higher`; the 5→10 s resolution is untested end to end |
| Two parameters in one phrase | `Damage higher` + `Cooldown higher` correct, plus a spurious `ProjectileSpeed` |
| Brand-new ability from text alone | model detects `Blink` at 0.517 with 0.215 false positives; not wired |
| Nonsense degrades | **FAILS at model level** — returns LightningStrike |

The last four in-game rows share one blocker: `WishCraft.Build.cs` has no `HTTP` or `Json` entry, so no C++
code calls `/rules` yet. **The model layer is built and measured; the transport is not built at all.** That
is a plan of its own (`llm_rule_layer_v1.md`, now stale — it still describes a float-parameter contract and
a provider-pinned endpoint). Calling this "done" because the Python works would repeat the P4 mistake:
a harness that cannot exercise the path is not evidence the path works.

### The transferable lesson

Every number in this entry that changed a decision came from measuring the obvious thing that looked fine.
Bucket accuracy looked healthy at 0.829 until the majority-class rate was counted. Presence looked weak
until family-level rows showed it was *two* failures wearing one number. The threshold sweep cost less than
a retrain and ruled out the cheapest explanation first. **A metric without its trivial baseline is not a
result** — and a metric averaged over a mixed test set hides which half is broken.

---

## P12 — Rebuilding the bucket head, and the measurement that was lying

### What started this

P11 ended with "the bucket head has no skill" and blamed the class weighting. That was a conclusion drawn
from one number — bucket accuracy — and it was wrong in two separate ways. This session re-opened it by
asking the model directly, phrase by phrase, instead of trusting the summary.

`Saved/probe_buckets.py` loads `Tools/intent_model/model.pt` and runs `train.predict` on the four Verify
phrases, printing the presence score for every candidate, the buckets emitted at the shipped 0.8 confidence
floor, and the golden label beside it. Run against the **pre-change** checkpoint (epoch 7), what it printed
changed the diagnosis:

| Phrase | Emitted | Truth | What is actually wrong |
|---|---|---|---|
| `punchy but slow fireballs` | Damage `higher`, Cooldown `higher`, ProjectileSpeed `higher` | ProjectileSpeed `default` | one param moved that nobody mentioned |
| `let me vanish twice as long` | Duration `higher`, Cooldown `higher` | Cooldown `default` | same |
| `everything except invisibility` | *(nothing)* | four tokens | presence never fired |
| `make me a sandwich` | LightningStrike, Fireball | *(nothing)* | confident false positive |

**There is no direction error anywhere in that table.** `twice as long` → `Duration higher` is correct;
`punchy` → `Damage higher` is correct. Every single bucket mistake has the same shape: the model moves a
parameter the sentence never mentioned. So the direction logic was not broken — the *"should this move at
all"* decision was. P11's "the model has not learned direction" was never measured, it was inferred from the
`ProjectileSpeed higher` row by assuming `slow` meant `lower`. It does not: the golden label for that phrase
leaves `ProjectileSpeed` alone entirely.

### The change: split one question into two

Before, the last layer of the bucket head was one `Linear(128, 5)` and a five-way softmax over
`much_lower / lower / default / higher / max`. That asks two different questions in a single vote, and
`default` is ~85% of the labels, so the four real answers had to fight a class that should never have been
in the same softmax. The `pow(0.2)` class weights in `bucket_class_weights` existed only to compensate for
that fight — and compensating for it taught the model to gamble on rare buckets, which is exactly the
spurious-parameter symptom.

The fix is in `train.py`, and the guiding idea is **mask, don't weight**:

- `schema.py` gains `DEFAULT_INDEX`, `MOVED_BUCKETS`, `MOVED_INDICES`, `MOVED_TO_INDEX`. The four buckets
  that mean "this parameter moves" get their own index space; `default` is deliberately not in it.
- `IntentModel.bucket_trunk` is the old 4×128 → 256 → 128 stack, but it now ends there. `moved_head` is
  `Linear(128, 1)` — "did this parameter move?" — and `direction_head` is `Linear(128, 4)` — "which way?".
- `compute_loss` asks the first question of **every** labelled row (plain BCE, no weights) and the second
  question **only of the rows that actually moved** (cross-entropy with the unchanged rows masked out). The
  direction head therefore never sees a `default` row, so the 85% prior cannot swamp it and there is
  nothing left to re-weight.
- `IntentModel.bucket_probs` reassembles a normalised five-way distribution,
  `p(default) = 1 − P(moved)` and `p(bucket) = P(moved) · P(bucket | moved)`. That is why every existing
  call site still just does `.argmax(-1)` over `BUCKETS` order and keeps working unchanged.

`Saved/model_prefactor.pt` is the pre-change checkpoint, kept so the before column stays reproducible.

### Wrong turn #1: I trusted a metric measured on the wrong split

First attempt kept a weight on the direction head, `pow(0.5)`, reasoning that `much_lower` and `max` are rare.
Training reported `dir 0.059` and it sat there. `0.059` looked exactly like the base rate of the rarest
bucket, so I concluded the head had collapsed onto `much_lower` and removed the weights.

The second run, unweighted, reported `dir 0.059` at epoch 1 as well. Identical. That could not be a real
model difference, so the metric itself had to be wrong — and it was. `dir` is computed on `valid.jsonl`, and
`valid.jsonl` is only `only_verb` plus `param_single_reduce`. Its moved labels are ~85% `lower`, a
distribution the model never sees in training, so `dir` there measures disagreement with the wrong question
and reads ~0.06 however good the head is. **The weighting was dropped for a reason that did not exist.**
(It was still the right call on design grounds — masking already removes the prior that mattered — but that
is a different justification, and the code comment says so rather than repeating the false one.)

So `evaluate.py` gained `bucket_head_report`: the two questions computed separately, from the raw heads, on
whichever split is passed in. `main()` runs it on `test.jsonl` and `golden.jsonl`. This is the metric that
decides whether the head works, and it is the one P11 should have had.

### What the retrain actually produced

Best epoch 10 of 14 (`--patience 4`), same 7,714 / 600 rows, same 1,371,399 parameters.

| Metric | P11 (epoch 7) | P12 (epoch 10) |
|---|---|---|
| `valid` exact match | 0.592 | **0.707** |
| `valid` presence F1 | 0.897 | **0.976** |
| `test` token-set exact | 0.464 | **0.480** |
| `test` intent exact | 0.143 | **0.174** |
| `test` bucket accuracy | 0.826 | **0.842** |
| `golden` token-set exact | 0.450 | **0.550** |
| `golden` intent exact | — | 0.150 |
| `param_single_superlative` bucket | 0.433 | **0.524** |
| held-out `Blink` false-positive rate | 0.215 | 0.257 (worse) |

One caveat on that table, because it is the kind of thing that makes a comparison look better than it is:
P11 was measured at `evaluate.py`'s old default confidence of 0.5 and P12 at 0.8, which is the floor
`serve.py` actually ships. The same checkpoint scores 0.473 at 0.5 and 0.480 at 0.8, so the measurement
change is worth about +0.007 and the model change carries the rest. `evaluate.py` now defaults to 0.8 so
the two columns cannot drift apart again.

Presence improving (`0.897` → `0.976`) without touching the presence head is the interesting part. The old
`pow(0.2)` bucket loss was a third of the total and its gradients flowed back into the shared phrase and
text encoders, so a noisy objective was degrading the part that was working. Removing it cleaned the
encoders up as a side effect. That is worth remembering: **a bad auxiliary loss does not fail locally.**

### What is still wrong, and why

The bucket head is now measured properly, and it still loses to doing nothing:

| Question | Model | Trivial answer |
|---|---|---|
| moved (did it move?) | 0.814 | 0.852 (always `default`) |
| direction (which way?) | 0.326 | 0.424 (always `higher`) |

`Saved/probe_heads.py` finds out where. The parameter and token vectors are fine (token cosines 0.29–0.51
apart, Fireball's `Damage` vs `Cooldown` 0.89), so the shared text encoder is not the problem. The head's
*output* is: it reads intensity correctly — `default` rises 0.66 → 0.71 → 0.86 → 0.90 as the modifier goes
`maximum` → `much more` → `normal` → `barely any` — but `p(higher | moved)` stays near 0.88 throughout, and
`max` never fires at all, returning `0.01` even for the literal input `maximum damage fireballs`.

Three causes, most important first:

1. **The label is barely in the corpus.** `max` and `much_lower` appear in exactly one training family,
   `param_multi_conflict`, and always inside a two-clause sentence competing with a second modifier. No
   training row says "maximum X" and means `max` alone — but `param_single_superlative`, a *test* family,
   asks for exactly that. This is coverage, not capacity, and it is a `generate_dataset.py` fix.
2. **The same word means opposite directions per parameter.** "slower" is `higher` for `Cooldown` and
   `lower` for `ProjectileSpeed`; "shorter" is `lower` for both `Duration` and `Range`. Resolving that needs
   the modifier word and the parameter name combined *jointly*, and concatenating two L2-normalised vectors
   does not hand the head that product cheaply. The architectural answer is attention from the parameter
   vector over the phrase's token sequence, so `punchy` and `slow` get bound to `Damage` and `Cooldown`
   rather than averaged together.
3. **Negative evidence is missing.** Nothing in training ever rewards *leaving a parameter alone* more than
   it rewards moving one, so `0.814` against a `0.852` prior is the scoreboard of a head that has not
   learned when to abstain.

### Verify status after P12

| Case | P11 | P12 |
|---|---|---|
| Beats the baseline on `golden.jsonl` | FAILS (0.450 vs 0.700) | **FAILS** (0.550 vs 0.700) — closer, still short |
| Complement works in game | FAILS (empty list) | **FIXED at model level**: 4 of 5 candidates now fire at 0.98–0.99 and `Invisibility` correctly scores 0.005 |
| Comparative lands in a bucket | partial | **WORSE**: `let me vanish twice as long` gives `Duration default` (should be `higher`) |
| Two parameters in one phrase | `Damage`+`Cooldown` right, spurious `ProjectileSpeed` | **WORSE**: both `default` — refuses to move anything |
| Brand-new ability from text alone | 0.517 detect / 0.215 FPR | 0.493 detect / **0.257 FPR** |
| Nonsense degrades | FAILS | **FAILS** — `make me a sandwich` still returns two abilities at 1.000 and 0.892 |

Two of those need saying plainly. The complement case genuinely improved: `everything except invisibility`
now returns four abilities with the excluded one at 0.005, which is the set-relative behaviour the MLP
presence head was built for and the previous checkpoint could not produce. But the bucket case regressed,
because an unweighted `moved` head on a 15%-positive task learns to abstain, and `argmax` then never lets a
non-`default` bucket win. **Fixing the spurious-parameter failure overshot into never moving anything.**

### The threshold sweep, which ruled out the cheap explanation

The natural next move is to lower the decode threshold. It costs no retrain: `bucket_probs` returns a
normalised five-way distribution, so `default` wins by `argmax` unless `P(moved) > 0.5`, and 0.5 is a
convention rather than a tuned value. `Saved/sweep_moved.py` caches one forward pass and re-decodes at every
threshold, with `tau = 1.01` standing in for the trivial all-`default` predictor:

| tau | `test` intent | `test` bucket | `golden` intent |
|---|---|---|---|
| 1.01 (all `default`) | **0.246** | **0.852** | **0.100** |
| 0.50 | 0.245 | 0.835 | 0.100 |
| 0.45 | 0.244 | 0.828 | 0.100 |
| 0.40 | 0.243 | 0.819 | 0.100 |
| 0.30 | 0.232 | 0.791 | 0.100 |
| 0.20 | 0.216 | 0.757 | 0.100 |

The ordering is monotone and it runs the wrong way. Forcing the head to move parameters only ever costs
accuracy, so the threshold is not the missing knob, and the honest statement is that **this head is worse
than not having one** on this corpus. That is a more useful finding than "needs tuning", because it closes
off the cheap fix: the next change has to be the corpus (cause 1) or the word-to-parameter binding (cause 2),
not a decode constant.

A side effect worth recording. The sweep reported `test` token-set 0.587 while `evaluate.py` reported 0.480,
for the same checkpoint at the same floor. Neither is wrong: `evaluate.py` deliberately adds the held-out
`Blink` row back, and the presence head reads the candidate *set*, so a sixth row changes every score. The
0.257 false-positive rate is the price of that harder task, not evidence of two different models.

### The transferable lesson

A single metric hid two different bugs, and then a second metric measured on `valid.jsonl` invented a third
that did not exist. Both times the fix was to stop averaging and measure the specific thing: which parameter,
which phrase, what probability. **A factorised head is worth building for the measurement alone** — the
moment `moved` and `direction` were scored separately, the model's real failure (`0.814` vs a `0.852`
constant) became visible, and the story P11 told about "no direction skill" collapsed under one direct probe.

The other lesson is sharper and is the second instance of it in this journal: **a weight you added to fix a
symptom is a symptom of a measurement you got wrong.** `pow(0.2)` was compensating for a class imbalance
that should have been removed structurally, and it manufactured a new failure while hiding the real one.

## P13 — The split was teaching a constant

### What started this

P12 closed with two ranked causes and one cheap untested thing. Cause A was written as "corpus
coverage: `max` and `much_lower` appear in one training family only". That was true and still
understated it. Reading `generate_dataset.py`'s control flow instead of trusting the summary
turned a coverage complaint into a labelling bug.

### The defect

`prepare()` handled `param_single` by sampling a bucket from the prior — and then overriding it by
family, three lines later:

```python
if family_name.endswith("comparative"):   s.buckets[name][param] = "higher"   # train
if family_name.endswith("reduce"):        s.buckets[name][param] = "lower"    # valid
if family_name.endswith("superlative"):   s.buckets[name][param] = "max"      # test
```

The split, not the phrase, was deciding the label. So the only clean single-parameter direction the
model ever saw in training was `higher` — and it answered `higher` for every parameter of every
phrase (`p ≈ 0.88`), while `max` sat at `0.01` even for the literal input `maximum damage`. Both
P12 symptoms are one cause.

It also broke the file's own contract, stated at `generate_dataset.py:504`:

> Every kind appears in BOTH train and test so the test score reflects an unseen surface form
> rather than an unseen concept.

For `param_single` the split varied the **concept**. The rule was violated by the one family where
it mattered most, and nothing in the code enforced it.

*Measured, single-parameter label counts — the fix in one table:*

| Split | Before | After |
|---|---|---|
| train | `higher` only | `higher` 540, `lower` 391, `max` 267, `much_lower` 202 |
| test | `max` only | `higher` 81, `lower` 51, `much_lower` 29, `max` 26 |
| valid | `lower` only | `higher` 134, `lower` 116, `max` 42, `much_lower` 35 |

### The fix

1. The bucket is drawn once in `prepare()` and **never overridden by family**.
2. The split now varies the **wording**: training renders from the generic intensity pool, the test
   and valid splits render the same buckets through the curated per-parameter vocabulary.
3. `param_single_comparative` / `_reduce` / `_superlative` keep their names — they identify the
   split now, and renaming them would break comparability with the published per-family tables.

### Wrong turn #1, caught by the generator's own audit

The first version kept shared frame strings (`max {param} {word}`) for both splits, so the two
sides collided: **93 test sentences appeared verbatim in training.** That is memorisation wearing
a test-score costume. Fixed by giving each split disjoint syntactic frames *and* filtering curated
adjectives the generic pool can also produce — `"maximum damage"` is in both lists, so evaluation
drops it. Train/test and train/valid overlap are back to **0**, asserted on every generation.

### What the retrain produced

Best epoch 11 of 15 (`--patience 4`), 1,373,191 parameters, same 7,714 / 600 rows.

| Metric | P12 | P13 | Constant baseline |
|---|---|---|---|
| direction, `test` | 0.326 | **0.637** | 0.447 |
| `moved`, `test` | 0.814 | 0.852 | 0.858 |
| `test` token-set | 0.480 | 0.501 | — |
| `test` intent | 0.174 | 0.179 | — |
| `golden` token-set | 0.550 | 0.500 | — |
| `golden` intent | 0.150 | 0.200 | — |
| `param_single_superlative` bucket | 0.524 | **0.613** | 0.638 |
| `param_multi_agree` token-set | 0.690 | **0.850** | — |
| held-out `Blink` | 0.493 / 0.257 FPR | 0.286 / 0.216 FPR | — |

**Direction went from 0.77× its constant to 1.42×.** That is the first part of this model that has
shown skill rather than a bias, and it was predicted before the run: if the corpus were the binding
constraint, direction should move toward or past its baseline *with no change to the model*. It did.

### One caveat that has to travel with that table

The corpus was regenerated, so the rows are not the same across columns and per-family numbers move
for reasons unrelated to the model. The two structural metrics are the honest comparison because
each carries its own constant computed on the same rows; everything else is directional at best.

### What this did not fix

- **`moved` did not move** (0.814 → 0.852, constant 0.858). The fix never touched that head, which
  is now good evidence that *when to abstain* is a different problem from *which way to go*.
- **`max` is reachable but not yet better than guessing** — 0.613 against a 0.638 majority.
- **Polarity is now the top confusion.** `Cooldown` truth `lower`/`max` read as `higher`, and
  `ProjectileSpeed` inverted. That is the attention-from-parameter-vector work, still unbuilt.
- **Held-out `Blink` regressed badly** (0.493 → 0.286 detection) and nothing about the presence head
  or token distribution changed. It was flagged here as unexplained rather than guessed at, and
  `Saved/probe_blink.py` then resolved it in two measurements:

  1. **Not calibration.** Threshold-free AUC **0.687 → 0.581**, with the whole detection/FPR curve
     below P12's at every threshold — so it is a real loss of separation, not a lowered scale.
  2. **Not general presence.** Per-candidate AUC on `test.jsonl`: `Fireball` 0.674 → 0.787,
     `LightningStrike` 0.702 → 0.767, `Invisibility` 0.761 → 0.745, `Shield` 0.765 → 0.713,
     `SpeedBoost` 0.808 → 0.742. Presence *improved* on two candidates. The damage is confined to
     the one token that has to be recognised with no training exposure at all.

  Likely cause, still unproven: the new frames are more formulaic (`max damage fireball`,
  `less damage on fireball`), so presence can be solved by template surface pattern instead of by
  character similarity to the candidate name — which is the trigram channel an unseen name depends
  on. In-vocabulary accuracy rising while zero-shot falls is what that trade looks like. The fix is
  to restore lexical variety in the frames, not to revert the label fix.

### The transferable lesson

A label that is a function of the split is invisible to every metric you have, because the metric is
computed against a corpus that has already absorbed the bug. Loss fell smoothly the entire time this
was true. No accuracy number, no confusion matrix and no loss curve would have found it — reading
the generator's control flow did. **When a head looks like it has a prior rather than a skill, check
whether the labels were conditioned on the split before you touch the architecture.**

That is the third instance in this project of the same shape of error: the confidence-floor mismatch,
the `valid.jsonl` selection, and now this. In all three the experiment was wrong, not the model.

## P14 — Attention from the parameter vector

### What was actually broken

P13 left two structural defects. Polarity was the top confusion class: `Cooldown` truth `lower`
read as `higher` (x31) and `ProjectileSpeed` inverted. And `moved` still sat a hair *below* a
constant — 0.852 against 0.858 — meaning the head had no skill at deciding whether a phrase touches
a parameter at all; it was reciting the base rate.

Both symptoms have one cause. The bucket head received the phrase as **`peak`**, a single
max-pooled vector, plus the parameter name, concatenated. Max pooling picks the strongest word in
the phrase *without knowing which parameter is being asked about*, so "slower" arrives as the same
vector whether the question is `Cooldown` or `ProjectileSpeed`. The head could only memorise each
pairing, and it never had the information to do better. That is why "slower" is `higher` for one
and `lower` for the other: it learned one of them and guessed on the other.

### The change

Three edits to `train.py`, one to each call site:

1. `Encoder.forward` gained `return_states`, returning the per-token hidden states and the padding
   mask instead of a pooled vector. Anything that needs to know *which* word said something has to
   come through here; pooling has already discarded it.
2. `IntentModel` gained `param_attention` — one `nn.MultiheadAttention` — and an `attend()` method
   whose **query is the parameter-name embedding** and whose key/value is the phrase token sequence.
   The output is a phrase summary conditioned on which parameter is being asked about.
3. `bucket_heads` takes `context` where `peak` used to be, at the **same width**. The trunk, the
   two factorised heads and every decode path are unchanged in shape and index semantics, so the
   `BUCKETS` order invariant never came into question.

One detail worth recording because it is the difference between the head working and looking like it
did nothing: `attend()` passes `key_padding_mask`. Without it the padding tokens collect attention
weight from every query, the context drifts toward a constant, and the result is a model that trains
perfectly happily and has learned nothing.

One performance detail: in `evaluate.py` the context depends on (row, candidate, parameter), so each
parameter slot is attended separately — 6 iterations of `count x candidates` — rather than expanding
the key/value sequence across the whole `rows x candidates x slots` grid, which is the same arithmetic
for about 12x the memory (~170 MB at 1,166 rows).

### Does the gradient reach it

Before spending a training run, `Saved/smoke_attention.py` runs one forward, backward, evaluate and
`predict`. `param_attention.in_proj_weight.grad` came back non-`None` with norm 0.0215, so the new
module is in the graph and not quietly detached — the failure mode a shape-correct refactor can hide.
Parameters went 1,373,191 → 1,432,327 (+59,136, the attention block plus its LayerNorm).

### What the retrain produced

Run 6, `--epochs 16 --patience 4`, best **epoch 14**:

| Metric | P13 | **P14** | Constant baseline |
|---|---|---|---|
| direction (`test.jsonl`) | 0.637 | **0.675** | 0.447 |
| `moved` (`test.jsonl`) | 0.852 | **0.869** | 0.858 |
| bucket accuracy (`test.jsonl`) | 0.858 | **0.879** | 0.858 |
| intent exact (`test.jsonl`) | 0.179 | **0.234** | 0.323 |
| token-set exact (`test.jsonl`) | 0.501 | 0.485 | 0.652 |
| intent exact (`golden.jsonl`) | 0.200 | **0.300** | 0.250 |
| valid exact | 0.715 | **0.837** | — |
| held-out `Blink` | 0.286 / 0.216 FPR | 0.366 / 0.291 FPR | — |

`moved` now clears its constant for the first time (0.869 against 0.858), and direction is 1.51x its
constant where the bucket head could never previously get past 0.77x. **The headline that matters is
`golden.jsonl`: the model beats the keyword baseline on intent exact, 0.300 to 0.250.** Twenty rows
is a small sample and this is not a victory lap, but it is the first time the model has done anything
the parser structurally cannot.

### What it did not fix, said plainly

The model still loses to the baseline on `test.jsonl` intent exact, 0.234 to 0.323. Polarity is
*still* the top confusion (`Cooldown` truth `lower` → predicted `higher` x22, `Cooldown` truth `max`
→ `lower` x18, `Cooldown` truth `higher` → `lower` x16) — reduced, not solved. Token-set exact fell
slightly, 0.501 → 0.485. And `Blink` detection rose to 0.366 but so did its false-positive rate, to
0.291, so that pair should be read as one number rather than a win.

A caveat on comparison: the P13 AUC measurement cannot be repeated across this boundary. P12 and P13
predate `param_attention`, so their checkpoints no longer load into the current architecture — the
held-out comparison after P14 is the detection/FPR pair only.

### The transferable lesson

A pooled representation of a sentence and a vector naming *which part* of it you care about are not
interchangeable inputs. If the answer depends on an interaction between a word and a label
("slower" meaning opposite things for two parameters), the interaction has to exist somewhere in
the network — either in a hand-built product, or in an attention query. Concatenating the two and
hoping a small MLP discovers the product is not a neutral default: it silently converts a reasoning
problem into a memorisation problem, and it costs a training run and two evaluations to notice.


## P15 - Teaching the model to say nothing, and the bill for it

### What was broken

The presence head had no "none of these" answer available to it, because no training row had ever
carried one. Every sentence in the corpus named at least one candidate, so template vocabulary
(`make`, `give me`, `reduce`, `higher`) was always co-present with a real referent. The head
learned the only rule the data supported: these words predict *something*, and the surrounding
tokens decide which. Handed `make me a sandwich`, it fired on SpeedBoost at confidence 1.000.

That is not a calibration problem and the confidence floor cannot fix it. The head was certain and
wrong, and it had never been shown the alternative.

### The change

Two train-only families whose correct label is the empty set, about 9% of training rows:

- `abstain_template` - the exact request verbs and intensity adjectives, applied to something that
  is not in the catalogue: `make the music louder`, `increase the volume`, `turn the hud down`.
  Reusing the real vocabulary is the entire point; a negative built from unusual words teaches the
  model that unusual words are what makes a phrase a non-request.
- `abstain_ood` - a request frame around something outside the game: `make me a pizza`,
  `where is the wifi`. The two phrases used to measure the result are deliberately absent from the
  topic list, so the improvement is generalisation rather than memorisation.

Parameter words were kept out of both lists on purpose. `make the cooldown lower` with no ability
named is a genuinely ambiguous request, not a non-request, and labelling it "nothing" would teach
the model to ignore a real signal.

Two call sites had to be told an empty label is legal. `validate_selection` rejects it, correctly,
because there is nothing to equip - so `generate()` skips that check for these families, and
`audit_split()` inverts it: an abstain row carrying a label is the bug. The trainer needed no
change. `Tensors` already produces an all-zero presence row for a label with no tokens, and
`compute_loss` already masks rows with no bucket targets.

The families are train-only, which breaks the file's standing rule that every kind appears in both
splits. Folding empty-label rows into `test.jsonl` would move the denominator of every existing
metric and destroy comparability with P13/P14 for no gain; the number worth having is the
false-positive rate on distractors the corpus has never seen, which `Saved/probe_abstain.py`
reports separately.

### Did it work

On its own metric, completely. At the shipped 0.8 floor:

| Phrase | P14 | P15 |
|---|---|---|
| `make me a sandwich` | SpeedBoost, 1.000 | 0 tokens, 0.000 |
| `what is the weather tomorrow` | 4 abilities | 0 tokens, 0.000 |
| 8 further held-out distractors | not measured | 7 of 8 abstained |
| 6 control requests | not measured | 6 of 6 preserved |

The control group is what makes the number mean anything. Silence is trivially achievable by
learning to answer nothing to everything, so the objective only counts as met because real requests
kept resolving. The single remaining false positive is `drop the volume a little` -> Shield at
0.994, which is the same failure shape as before: an intensity cue with no referent, resolved by
guessing. One of eight rather than all of them.

### What it cost

Best epoch 14 of 16, valid exact 0.768 (P14: 0.837). On `test.jsonl`:

| Metric | P14 | P15 | Change |
|---|---|---|---|
| intent exact | 0.234 | 0.226 | -0.008 |
| token-set exact | 0.485 | 0.421 | -0.064 |
| bucket accuracy | 0.879 | 0.857 | -0.022 |
| `moved` | 0.869 | 0.810 | -0.059 |
| direction | 0.675 | 0.589 | -0.086 |

`moved` at 0.810 is below the always-default constant of 0.852. The head that decides whether a
parameter moved now loses to predicting "unchanged" on every label, which is a regression P14 had
specifically fixed by 0.011.

Attribution is not clean. Regenerating the corpus to add the families re-sampled every existing
family from one seeded stream, so the baseline moved too (intent exact 0.323 -> 0.304, token-set
0.652 -> 0.648). An unknown fraction of the model's loss is corpus resampling rather than the
negatives. The controlled comparison - same corpus, abstain families disabled - has not been run.

### What it did not fix

Complements are unchanged or slightly worse: `everything except fireball` still excludes correctly
but returns 2 of the 5 abilities where P14 returned all 5, and the two `but` / `all abilities
except` phrasings still return the excluded token. The abstain work touches presence suppression;
it does not touch the surface-form dependence that makes a complement work in one wording and fail
in the next.

### A process failure worth recording

P14's checkpoint and corpus were overwritten by this run. There is no git repository here, and
`model.pt` was replaced on the first improving epoch, so P14 is not recoverable from disk. It is
recoverable only by reverting the generator edits and regenerating, since generation is seeded -
which is 20 minutes of work that a single `copy model.pt` before launching would have avoided.

### The transferable lesson

A change has to be priced against the metrics it was not aimed at. Abstention was the top-ranked
defect and the fix removed it outright - both failing phrases silent at exactly zero confidence,
with every control intact - and it still made the model worse, because nine percent of the corpus
went to teaching one head to suppress and that head shares its encoder with the bucket trunk.
Owning the defect is not the same as owning the fix. The measurement that decides is the one the
plan does not mention.


## P16 - The test set was moving, and the abstain bill paid down

### What P15 got wrong that had nothing to do with abstention

The plan was to price the abstain objective with a control run: same generator, abstain families
switched off, same seed. That control produced a test baseline of **0.652 / 0.323** - P14's numbers
*exactly*, when the corpus had just been regenerated and the baseline should have read something
else. A coincidence that precise is never a coincidence.

The cause: all three splits drew from ONE seeded stream. `generate_dataset.py` created
`rng = random.Random(seed)` and then handed that same object to the train loop, the valid loop and
the test loop in sequence. Adding a training family consumes draws, which shifts the state every
later split reads, so the test sentences themselves changed. Removing the abstain families restored
the pre-P15 state and therefore reproduced the P14 corpus byte for byte.

Consequence: every P13/P14/P15 comparison had been scored against a slightly different exam, and
the P15 regression I reported (token-set 0.485 -> 0.421) was measuring two changes at once. The
lesson generalises past this project - a shared RNG across splits makes a benchmark
self-referential, and nothing about the numbers looks wrong while it happens.

### The fix, and how it was verified

Each split now draws from its own stream: `args.seed` for train, `+1` for valid, `+2` for test,
`+3` for heldout, with a matching `random.seed(...)` before each loop because `intensity_words`
still draws from the module RNG.

Verified by generation, not by inspection: generate with the abstain families, hash `test.jsonl`,
generate with `--skip-abstain`, hash again.

    with abstain    test 6734c287c5447c23
    without abstain test 6734c287c5447c23

Identical. Re-checked later under a different `--train` value: still identical. The test split is
now invariant to anything done to training, which is the property that makes every number below
worth reading.

### Then the abstain allocation, priced properly

Three changes, each with a reason:

1. **Per-split RNG** (above).
2. **Lopsided weights**: `abstain_ood` 0.3, `abstain_template` 0.15. The two kinds do not cost the
   same. `make me a pizza` shares no vocabulary with a request, so it is cheap and it carries the
   case a player actually hits. `make the music louder` is deliberately maximally confusable - that
   is what makes it effective and what makes it expensive. P15 ran them 6.3% / 2.3%; P16 runs
   3.6% / 1.8%, 5.3% total instead of 9%.
3. **Additive budget** (`--train 8440`, 8,143 rows). P15's negatives *displaced* real labelled rows:
   roughly 7,319 against the control's 7,714. Part of the "cost of abstention" was just less
   supervision.

### Where it landed

Control = identical generator, abstain families off, same stable test set, 7,714 rows, best epoch 14.
P16 = 8,143 rows, best epoch 15, valid exact 0.750 / presence-F1 0.936 / bucket 0.854 /
`moved` 0.888 / direction 0.662.

| Metric (`test`) | control | P16 |
|---|---|---|
| intent exact | 0.236 | **0.241** |
| token-set exact | 0.479 | 0.437 |
| bucket accuracy | 0.876 | 0.872 |
| `moved` | 0.874 | 0.823 |
| direction | 0.668 | **0.673** |
| intent exact (`golden`) | 0.300 | 0.300 (baseline 0.250) |
| bucket accuracy (`golden`) | 0.839 | **0.871** |
| `moved` (`golden`) | 0.817 | **0.860** (constant 0.839) |

Abstention: 8 of 10 held-out distractors silent, both P14 failures silent at 0.000, 6 of 6 control
requests preserved. The two remaining false positives are `drop the volume a little` and
`set the hud brightness to dark` - the confusable family, as expected.

Held-out `Blink`: detection 0.152, FPR 0.136. Both low; the suppression is not targeted at Blink.

An intermediate configuration was also trained and is kept at `Saved/model_p16a.pt`: 5.3% lopsided
but *without* the additive budget (7,729 rows, 7,319 real). It scored better on `moved` (0.841) and
worse on intent exact (0.226), direction (0.641), golden bucket (0.860) and abstention (7 of 10).
The additive budget was kept because it is better on four metrics of five and on abstention, but the
`moved` result moving the *wrong* way when more data was added is unexplained and is the first thing
to look at next.

### It is now serving, not just scoring

Scoring a checkpoint and serving it are different claims, so the real HTTP path was exercised:
`uvicorn serve:app` on 127.0.0.1:8077, then `Saved/serve_smoke.py` posting the catalogue rows the
way the game will.

    health: ok, checkpoint ...model.pt, vocab 269 words, floor 0.8
    'make the fireball do more damage'  -> Fireball, Damage=max, ProjectileSpeed=higher
    'only the shield'                   -> Shield
    'no invisibility'                   -> 4 tokens, Invisibility absent
    'make me a sandwich'                -> []
    'make the music louder'             -> []

Contract holds: `{"tokens": [{"name", "params"}]}`, buckets only, and an empty list for input that
names nothing - which is what lets the caller fall back to the keyword parser instead of equipping
something at random.

### What is left

`moved` on `test` at 0.823 against a 0.858 constant is the last metric where the model loses to a
trivial predictor, and it is inconsistent with `golden` clearing its own constant (0.860 against
0.839), so the head is not simply broken. The C++ transport is still absent: `WishCraft.Build.cs`
has no `HTTP`/`Json`, so the model is served but not yet reachable from the game.

### The transferable lesson

Before comparing two runs, prove they were scored on the same data. This project spent a whole
training run and a write-up attributing a regression to a data change, when part of it was the
test set silently resampling underneath. The tell was a control run returning historically exact
numbers; the fix was four lines of seeding; the cost of not having it was three runs of conclusions
that had to be withdrawn.

## P17 - Connecting the served model to the game

### Discovery (Source)

`AWishCraftPlayerController::ApplyRuleText` still calls `URuleParserLibrary::ParseRuleText`
synchronously and hands token names to `UAbilityLoadoutManager::ServerSetLoadout`.
`WBP_RuleInput` immediately displays the returned Boolean as success or failure. No HTTP
request exists in that path, and `WishCraft.Build.cs` has no HTTP or Json dependency.
The editor reports UE 5.8.4; HTTP interfaces were checked against that installed source.

### Implementation in progress

Add an asynchronous controller submission entry point and completion notification without
changing the synchronous keyword parser's contract. Send the actual loaded token table in
requests. Validate model JSON and convert symbolic buckets inside `ULoadoutValidator`, then
reuse the manager's GameState publication path. Keep keyword fallback for service failure
or abstention; never publish a malformed model response or an empty fallback selection.
Cancel outstanding HTTP work on EndPlay and prevent overlapping submissions.

### Progress and current blocker (Source / Measured)

The C++ files now contain the HTTP request, the intent JSON validator, the common ruleset
publication path, and an automation test for bucket conversion and invalid responses. (Source)
Live Coding reported that compilation and patching succeeded. (Measured) A live Python check
could not access `SubmitRuleText` or `RulesModel`; the delegate and pending flag were present.
This check does not by itself establish why the functions were unavailable. (Measured)
A full rebuild/restart was requested to load the reflected API, but was declined because this
chat is running inside the editor and would close with it. No rebuild or restart occurred.
At that point the rule widget was not yet wired to the async API, and tests had not run.

### Rebuilt API and validation test (Measured)

The user rebuilt the editor. A read-only check then found `submit_rule_text` on the controller,
with `OnRuleResolution` and `bRuleRequestPending` present. The Python name `rules_model` was
not exposed by that check; this does not prove the console command is unavailable.
The Unreal automation test `WishCraft.Intent.Validation` ran in the open editor: 1 passed,
0 failed. It checks bucket mappings, clamping, invalid responses, and category limits. This
is not yet an end-to-end game or network test.

### Rule panel connected (Source)

`/Game/Sandbox/UI/WBP_RuleInput` now calls `SubmitRuleText` when the host clicks Send. It
binds `OnRuleResolution` before submission, displays the actual completion message in green
or red, and shows `Checking rules...` only if the request is still pending. That last check
prevents a fast fallback completion from being overwritten by a waiting message. The widget
removes old delegate bindings on rebind and destruction. The Blueprint compiled and saved;
its runtime behavior and service connection have not yet been verified.

### Starting the model service (Measured)

Nothing was listening on port 8077, so we started `Tools/intent_model/serve.py` in a
background Uvicorn process. Its `/health` endpoint returned `status: ok`, the project
`model.pt` checkpoint, 269 vocabulary words and a confidence floor of 0.8. This proves the
service loaded the checkpoint; it does not yet prove the game can submit and apply a rule.

### Service response and PIE verification blocker (Measured)

A direct `/rules` HTTP request containing the Fireball definition from
`/Game/Sandbox/DA_AbilityTokens` and the text `fireballs` returned Fireball with default
Damage, ProjectileSpeed and Cooldown buckets. This tests the service independently, not the
Unreal HTTP connection or replicated game state.

Symptom: Automated PIE verification returned inconclusive before testing the claim. Cause:
its configured model `gpt-6.1-terra` was unavailable to the runner. Fix: gameplay still
needs a live PIE check through the rule panel after correcting the runner configuration or
starting PIE interactively. Tradeoff: service and validator tests are useful but cannot
prove in-game UI, host restrictions or client replication. Evidence: automation report
stated `claim=untested`; a live widget snapshot also reported no PIE world.

### End-to-end model path working (Measured)

The first live run fell back because the service process died when its parent shell exited:
the log showed `connect to 127.0.0.1 port 8077 failed: Connection refused`. Restarting it in
its own console window kept it alive, and `/health` answered again.

A rule entered through `WBP_RuleInput` then resolved through the model. Log evidence:
`Intent request started: make the fireball do maximum damage`; three `Intent resolved` lines
(Damage max -> 100, ProjectileSpeed max -> 5000, Cooldown default -> 1.5);
`Ruleset replicated: Fireball`; and `Ruleset applied ... (authority: no)` plus
`Ability Fireball configured ... (authority: no)` on the client. Reading the game state
confirmed one spec: Damage 100, ProjectileSpeed 5000, Cooldown 1.5.

This also exposes the model's known over-moving defect in the real game: the request asked
only for maximum damage, and ProjectileSpeed was moved to its maximum as well. (Measured)

Observed UI quirk: presses sent immediately after typing in the text box did not fire the
button; a later press did. Not yet diagnosed. (Measured)

### P17a - Ceiling requests in the corpus (Measured)

Symptom: `make the fireball do maximum damage` pushed ProjectileSpeed to its ceiling as well
as Damage. Cause: the label shape was never the problem. `generate_dataset.py` rendered the
`max` bucket in training only through the generic pool (`max damage fireball`), so no row
showed `maximum` inside a spoken frame next to a second parameter that had to stay put.
Fix: a train-only family `param_single_max`, registered next to `param_single_comparative`
with weight 1.0, that pins the bucket to `max`, keeps every other parameter of the token at
DEFAULT, and renders spoken frames including the exact failing shape.

Tradeoff and evidence, stated plainly: validation improved a lot (moved 0.948, exact 0.803)
but the honest surface did not. On `test.jsonl` the split-by-question report gives moved
0.800 against the 0.858 always-default constant -- still losing, and slightly worse than the
0.823 before this change. So the aggregate "does it move?" behaviour over all candidate
slots is NOT fixed. What is fixed is the user-facing case: the same service, restarted onto
the new checkpoint, returns Fireball Damage `max` with ProjectileSpeed and Cooldown
`default` for the failing sentence, and moves exactly one parameter for `maximum damage
fireball`, `push the projectile speed on the fireball all the way up` and `make the
invisibility have the most duration`. `make me a pizza` still returns no tokens.

Dataset integrity held: `test.jsonl` and `test_heldout.jsonl` hashes are unchanged
(6734c287c5447c23... and a9735b40...) because each split draws from its own seeded stream.
Train fell from 8,143 to 7,761 rows; `category_only` still exhausts its surface at 14 of 254.
Best epoch 18 of 30; checkpoint backed up first as `model_p16_backup.pt`.
Remaining bucket defect visible in the eval: `less damage on the fireball` returns
`much_lower`, i.e. direction overshoot.

### P17b - The floor sweep, and the eval surface itself (Measured)

Symptom under investigation: on `test.jsonl` the model picked the wrong ability set more
often than the keyword parser (token-set 0.417 against 0.664), so the first decision it
makes was worse than the thing it replaced.

Hypothesis 1, the confidence floor: maybe `INTENT_CONFIDENCE = 0.8` in `serve.py` was
suppressing real tokens. Ruled out. `evaluate.py --confidence` across 0.40, 0.50, 0.60,
0.70, 0.90 (against 0.8 already measured) gave token-set 0.415, 0.414, 0.412, 0.416,
0.415 -- flat within 0.005 -- and intent exact flat at 0.176-0.181. Lowering the floor
buys nothing and only raises held-out `Blink` false positives (0.255 at 0.9 to 0.306 at
0.4). The presence head's ranking is the issue, not its threshold.

Hypothesis 2, the eval surface: the reported deficit was measured against all six rows of
`catalogue.json`, but the game ships three. `/Game/Sandbox/DA_AbilityTokens` holds
Fireball, Invisibility and SpeedBoost, and `catalogue_live.json` mirrors it. Two of the
six candidates (`LightningStrike`, `Shield`) cannot be equipped, and `Blink` is the
deliberate never-trained token. Confirmed with the new `Tools/intent_model/eval_live.py`,
which scores only the live set and drops rows whose label names an ability the game
cannot equip (629 of 1166 dropped, 537 kept):

| live 3-token surface | keyword baseline | intent model |
|---|---|---|
| token set exact | 0.339 | 0.594 |
| intent exact | 0.128 | 0.253 |
| bucket accuracy | 0.797 | 0.822 |
| bucket acc on token match | 0.723 | 0.769 |

So on the candidates a player can actually choose from, the model beats the parser it
replaced by +0.255 token-set and +0.125 intent-exact. The P17 headline "loses to the
baseline by -0.136" describes a six-token task the game does not run. The six-token number
stays in `eval_report.json` for comparability with P11-P16; `eval_live.py` is the surface
that matches play.

What the live surface still shows broken, per family: `exclude_all_but` 0.000 and
`exclude_without` 0.015 token-set (negation is unsupported in BOTH paths -- the baseline
is 0.000 there too), `param_multi_agree` exact 0.041 with bucket accuracy 0.498 (coin
flip), `param_single_superlative` token-set 0.973 but exact 0.427 (presence solved, bucket
direction not).

### P18 and P19 - Two parameters together, and a vocabulary collision (Measured)

Target: `param_multi_agree` was registered as a `"test"` family with no training
counterpart. The only two-parameter training family was `param_multi_conflict`, which by
construction moves one parameter up and the other down, so "stronger and faster fireballs"
-- both up -- had never been trained. Added `param_multi_together` (train, weight 1.2).

P18 (first attempt) used `CURATED_INTENSITY` for its adjectives and that table is
phrase-shaped, so frames rendered "stronger and faster projectiles". Measured in the live
service: `make the fireball strong and fast` and `stronger and faster fireballs` both came
back all-default, while the parametric frames added alongside them worked
(`give the speed boost more duration and more multiplier` -> Duration higher, Multiplier
higher). P18 test-set intent exact rose to 0.285 and the moved head finally beat the
always-default constant (0.882 against 0.858), but the family itself was still broken.

P19 added a purpose-built `CURATED_BARE` table of one-word adjectives ("strong", "fast",
"longer", "quicker") for exactly this frame, verified by reading generated rows. Results,
against the previous checkpoints:

| surface | P17 | P18 | P19 |
|---|---|---|---|
| live 3-token intent exact | 0.253 | 0.304 | 0.313 |
| live 3-token token set | 0.594 | 0.546 | 0.544 |
| live bucket on token match | 0.769 | 0.826 | 0.835 |
| live param_multi_agree exact | 0.041 | 0.194 | 0.082 |
| six-token intent exact | 0.178 | 0.285 | 0.265 |
| six-token moved vs 0.858 | 0.800 | 0.882 | 0.901 |
| `param_single_superlative` exact (live) | 0.427 | 0.345 | 0.527 |

P19 ships: best live intent-exact, best bucket accuracy on token match, best moved head,
and the best superlative family. It costs the multi-parameter family relative to P18
(0.194 -> 0.082) and the six-token token-set. P18's weights were overwritten by P19's run --
the only retained checkpoint copy is `model_p17_backup.pt`.

Symptom after P19, measured in the live service: `make the fireball strong and fast` still
all-default, `stronger and faster fireballs` returns SpeedBoost plus Fireball (wrong
token), `give the speed boost quicker cooldowns and a bigger multiplier` returns Cooldown
higher instead of lower. Cause: the word `faster` is simultaneously the strongest presence
cue for SpeedBoost (whose keywords include `fast`, `speed`, `haste`) and the intended
adjective for Fireball's ProjectileSpeed-higher. Presence wins, so an adjective that should
set a bucket instead drags in a different ability. This is a label collision in the corpus,
not a threshold problem -- and it is why `param_multi_agree` stays weak however much of it
is generated.

What P19 does get right, verified live: `make the fireball punchy but slow` (Damage higher
+ Cooldown higher), `give the speed boost more duration and more multiplier` (both higher),
`make the fireball do maximum damage` (Damage max, everything else default), and
`push the projectile speed on the fireball all the way up`.

### P20 - Counting the `faster` collision instead of describing it (Measured)

**Symptom.** `stronger and faster fireballs` equipped SpeedBoost instead of Fireball.
`faster fireballs`, `slower fireballs`, `make the speed boost faster` and
`give me quick speed boosts` all returned the right ability with no parameter moved at all.

**Cause, measured rather than assumed.** `Saved/analyze_collision.py` counts the corpus rather
than reasoning about it. On the P19 train split (7,788 rows), 1,495 rows contain a speed
adjective and the label splits 820 SpeedBoost / 675 not. Per word, `faster` appears in 122
SpeedBoost rows and 164 non-SpeedBoost rows -- right 43% of the time, which is no signal at
all. The same audit found the reason the model used it anyway: **60 training rows use the word
`faster` as SpeedBoost's own name**, because `catalogue.json` lists `faster`, `quicker` and
`quick` among SpeedBoost's keywords and `build_alias_maps` promotes any keyword not shared with
another token into a surface name. So `param_multi_together` taught `faster` = Fireball's
ProjectileSpeed (SpeedBoost absent) while `pick_words` taught `faster` = SpeedBoost (present).
Contradictory supervision on one word. The P19 journal entry blamed `fast`/`speed`/`haste` in
the live keyword list; the training catalogue is the harder collision and this corrects it.

**Fix.** Additive only. A new train family `param_speed_contrast` (weight 1.0) renders BOTH
halves of the pair from one distribution -- `make the fireball fast` -> Fireball
ProjectileSpeed higher, and `make the speed boost blazing fast` -> SpeedBoost Multiplier higher
-- so presence keys on the noun rather than the adjective. Teaching only the Fireball half would
have fixed the failing sentence and broken `make the speed boost faster`, which already worked.
Verb frames name abilities by display name only (`_name_word`), because `pick_words` returns
catalogue keywords and several of Fireball's are verbs: the first build produced
`the shoot should be quick`.

Two fixes were rejected. Deleting `faster` from SpeedBoost's keywords removes a real request.
Making `build_alias_maps` refuse adjectives rewrites `test.jsonl`, because `pick_words` feeds
every family, which would void every number measured this session. Test hashes held:
`test.jsonl` 6734c287c5447c23..., `test_heldout.jsonl` a9735b40....

**Evidence.** `Tools/intent_model/probe_phrases.py` scores the actual phrases, because an
aggregate cannot show a fix that helps one sentence and breaks another. P19 4/13, P20 8/13.
`stronger and faster fireballs` moved from SpeedBoost to Fireball; `faster fireballs`,
`make the speed boost faster`, `faster speed boosts` and `give me quick speed boosts` all went
from "right ability, no parameter" to the correct bucket. Live 3-token surface, all four up:

| live 3-token | P19 | P20 |
|---|---|---|
| intent exact | 0.313 | 0.326 |
| token set exact | 0.544 | 0.575 |
| bucket accuracy | 0.859 | 0.864 |
| bucket acc, token ok | 0.835 | 0.839 |
| `param_multi_agree` exact | 0.082 | 0.173 |
| `param_multi_agree` token set | 0.520 | 0.745 |

`param_multi_agree` is the family the whole track exists for, and P20 beats P18's 0.194-era
position on token set while also beating P19 everywhere else. Superlative family dipped
0.527 -> 0.500 exact with bucket 0.810 -> 0.817.

**Tradeoff.** P20 loses one phrase it used to get: `make the fireball punchy but slow` returns
ProjectileSpeed lower + Cooldown lower instead of Damage higher + Cooldown higher. The new
family caused it, by the same mechanism it was fixing -- it taught `slow` as a downward
ProjectileSpeed word on Fireball, while `param_multi_conflict` renders `punchy but slow` as
Cooldown UP on that same ability. Also, `allocate` divides a split's rows across its families by
weight, so adding a family shrank every other one: `param_multi_together` fell 901 -> 824 rows.

### P21 - Three changes at once, and the negative result that followed (Measured)

Attempted to fix the P20 regression by withholding the downward speed branch wherever a bare
`slow` is already spoken for (`if {"Damage", "Cooldown"} <= set(params)`), AND raising
`param_multi_conflict` 1.4 -> 1.55 and `param_multi_together` 1.2 -> 1.35 to restore their
absolute row counts, AND dropping `param_speed_contrast` to 0.5 because it saturates at ~252-312
distinct sentences (at 1.0 it asked for 687 rows and produced 312 after 41,220 duplicate
rejections).

Result: worse than P20 on every aggregate and on the phrases.

| live 3-token | P19 | P20 | P21 |
|---|---|---|---|
| intent exact | 0.313 | 0.326 | 0.272 |
| token set exact | 0.544 | 0.575 | 0.551 |
| bucket accuracy | 0.859 | 0.864 | 0.853 |
| bucket acc, token ok | 0.835 | 0.839 | 0.817 |

Probes 6/13 against P20's 8/13. `make the fireball punchy but slow` got worse, not better --
nothing moved at all. `make the fireball do maximum damage` regained the ProjectileSpeed max
overshoot that P17 had fixed. `slower fireballs` was lost, which is the guard working as
designed and costing a phrase.

**Lesson.** Three simultaneous changes to a corpus where every family re-samples from one seeded
stream means the result identifies nothing. Weights reverted to P20's values; the guard is being
re-tested alone as P22 (written to `model_p22.pt`, not over `model.pt`, so the shipped
checkpoint cannot be clobbered by an experiment). P20 remains the shipped checkpoint.

**Service state.** `127.0.0.1:8077` restarted onto P20 and verified over HTTP with
`Saved/probe_service.py`: `/health` reports `vocab_words 308`, and the HTTP answers match
`train.predict` exactly for all five probe phrases.

### P22 - The guard alone, and the decision to reject it (Measured)

Same corpus weights as P20, one variable: withhold the downward speed branch on any token that
carries both `Damage` and `Cooldown` (i.e. Fireball), so `slow` stops meaning
ProjectileSpeed-down where `param_multi_conflict` already means Cooldown-up. Trained to
`model_p22.pt` so the shipped checkpoint could not be clobbered by an experiment. Best epoch 10.

It is worse. Probes 6/13 against P20's 8/13. `make the speed boost faster` **inverts** to
Multiplier LOWER. `give me quick speed boosts` returns no parameter. `make the fireball do
maximum damage` regains the ProjectileSpeed-max overshoot P17 had fixed. `slower fireballs` is
lost, which is the guard doing exactly what it was asked, and costs a working phrase.

| live 3-token | P20 | P22 |
|---|---|---|
| intent exact | 0.326 | 0.263 |
| token set exact | 0.575 | 0.585 |
| bucket accuracy | 0.864 | 0.849 |
| bucket acc, token ok | 0.839 | 0.800 |

P22 wins only token-set exact, and that is the least useful of the four: the right ability set
with the wrong buckets still needs every parameter corrected by hand, which is why intent exact
-- not token set -- is the metric that tracks playability.

**Decision: P20 ships.** Guard reverted, corpus regenerated back to the P20 state (train 7,433,
`param_speed_contrast` 312 rows, both test hashes unchanged at 6734c287... / a9735b40...), so the
shipped checkpoint stays reproducible from the corpus on disk. `model.pt` and
`model_p20_backup.pt` are byte-identical (MD5 6b69b29f4abcb4a9139155155e92912e).

**Retained checkpoints.** `model_p17_backup.pt`, `model_p19_backup.pt`, `model_p20_backup.pt`,
`model_p21_backup.pt`, `model_p22.pt`. P18 remains unrecoverable.

**Open, with evidence.** `make the fireball punchy but slow` is the one phrase P20 gets wrong
that P19 got right. Two corpus attempts to fix it (P21, P22) both made the aggregate worse, so
the next attempt should not be a third weight/corpus nudge -- the evidence says the conflict is
not where the speed family lives. `give the speed boost more duration and more multiplier` and
`quicker cooldowns and a bigger multiplier` still drop one of their two parameters, which is the
multi-parameter binding problem rather than a vocabulary collision.

### P23 - Naming an ability should change nothing (Measured)

**Symptom.** The PIE log from 12:48 shows `invisibility` arriving as `Duration lower -> 2.500`.
Probing the shipped service over `/rules` found three of six bare ability names moving a
parameter:

| typed | P20 returned | should be |
|---|---|---|
| `fireball` | ProjectileSpeed `lower` (2500 -> 1250) | nothing moved |
| `invisible` | Duration `lower` (5 -> 2.5) | nothing moved |
| `stealth` | Cooldown `lower` (8 -> 4) | nothing moved |
| `invisibility`, `speed boost`, `cloak` | nothing | correct |

That split is the diagnosis. It is not one bad word: P19 answered `invisibility` wrongly and
`fireball` correctly, P20 swapped them exactly. A rule the model actually held would not swap,
so the concept was missing rather than mis-weighted.

**Cause (measured, before any change).** `Saved/analyze_equip_only.py` on the P20 corpus: 5,076
of 7,433 train rows name exactly one ability, but only 1,108 of those leave every parameter at
`default` -- and the five `param_*` families contribute **zero** of them.

| family | single-ability rows | of which all-default |
|---|---|---|
| param_multi_conflict | 961 | 0 |
| param_single_comparative | 961 | 0 |
| param_multi_together | 824 | 0 |
| param_single_max | 687 | 0 |
| param_speed_contrast | 312 | 0 |
| only_suffix | 343 | 343 |
| exclude_everything_except | 400 | 314 |
| exclude_no_terse | 358 | 272 |
| request_want | 164 | 127 |

So 3,745 single-ability rows move a parameter and every one of them says so with an intensity
word. The plain shape a player types to *equip* something was thin and outnumbered 3.4 to 1.

**Fix.** New train family `equip_only`, weight 0.8, kind `equip`: one token, `mentioned_count=0`
so the label is exactly `default` by construction, 12 framed equip templates (`{word} please`,
`the {word}`, `i want {word}`, `equip {word}`, `put {word} on`, ...). The name is drawn from the
token's aliases minus every word `CURATED_BARE` or `SPEED_ADJECTIVES` uses as a bucket cue, so
no equip name can also mean "raise this" -- the P20 collision, in the opposite direction.
`Saved/check_equip_words.py` asserts zero live collisions across all six tokens (it excludes
`fast`/`faster`/`quick`/`quicker` from SpeedBoost's pool, which is the filter doing its job).

No bare `{word}` frame, deliberately. `allow_terse` (test) holds that exact string, and adding it
to train intersected the two splits on essentially the whole alias pool -- train/test overlap
went 0 to 36. Rendering bare names in train would have turned `allow_terse` into a memorisation
check and made the comparison meaningless.

**Corpus guard.** train 7,433 -> 7,492; both test hashes unchanged at 6734c287... /
a9735b40...; audit problems 0; train/test and train/valid overlap both 0.

**Tradeoff.** A new family divides the split, so every other family shrank:
`param_multi_conflict` 961->900, `param_multi_together` 824->771, `param_single_max` 687->643.
This is the same dilution that cost `more duration and more multiplier` when the contrast family
landed in P20 -- except this time it went the other way.

| metric | P20 | P23 |
|---|---|---|
| live intent exact | 0.326 | **0.363** |
| live token set exact | 0.575 | **0.594** |
| live bucket accuracy | 0.864 | **0.882** |
| live bucket acc, token ok | 0.839 | **0.854** |
| test token set exact | 0.472 | **0.524** |
| test intent exact | 0.249 | **0.297** |
| test bucket acc, token ok | 0.841 | **0.870** |
| gap to keyword baseline | -0.065 | **-0.017** |
| golden intent exact (n=20) | 0.300 | 0.250 |

Probes: 11/19 -> 15/19. Gained `fireball`, `stealth`, `invisible`-class equip requests,
`stronger and faster fireballs` (right token but no bucket since P20), and -- not targeted --
`give the speed boost more duration and more multiplier`, which had dropped a parameter since
before P20. Lost `slower fireballs`, which now returns nothing.

`punchy but slow` moved but is still wrong: P20 gave ProjectileSpeed-lower + Cooldown-lower,
P23 gives Damage-higher + ProjectileSpeed-higher. Damage-higher is now correct; the second
parameter is still the wrong pair, so this remains the binding problem, not a vocabulary one.

**Decision: P23 ships.** `model.pt` = `model_p23.pt`, MD5 `3db7d27c53bb86f0a3b03775a29e40a9`.
Service restarted onto it and verified over HTTP: `/health` reports `vocab_words 311`, `fireball`
and `stealth` return no parameter, `more duration and more multiplier` returns both.

**The wasted check.** The first version of `check_equip_words.py` printed "collisions remaining:
0" while its per-token detail printed nothing, because `CURRENT_TOKENS` is empty until
`configure()` is called and the loop never ran. A check whose loop body can be skipped reports
success for the same reason it reports anything. It now asserts the pool is non-empty before
claiming a pass.

### P24 - A family that was out of surface, not out of budget (Measured)

**Symptom.** P23 lost `slower fireballs`, which P20 answered correctly. One named phrase, known
cause candidate.

**A hypothesis that was true but not causal.** Counting the label mix first gave a striking
number: the corpus is **68.9% upward / 31.1% downward, 2.21:1**, and the imbalance is worst in the
families that exist to teach multi-parameter sentences (`param_multi_conflict` 32% down,
`param_multi_together` 29% down). That looked like the answer -- the model raising everything
because the data mostly raises things.

It is not the answer. `punchy but slow` should be Damage `higher` **and** Cooldown `higher` -- both
upward. A slow cooldown is one you wait longer for, so the correct label is a *raise*. There is no
buff/nerf confusion in that phrase at all.

What is confused is one word with two numeric directions. `CURATED_BARE` maps `slower`/`slow` to
ProjectileSpeed `lower` (a slow projectile) **and** to Cooldown `higher` (a slow cast). Both are
correct English; only the parameter can decide. (The tally that showed this is per-row, so a row
containing two adjectives attributes both labels to both words -- it inflates the ambiguity. The
`slower` ordering survives that distortion: Cooldown-higher 40% against ProjectileSpeed-lower 8%.)

**Cause (measured).** The family that binds `slower` to ProjectileSpeed explicitly is
`param_speed_contrast`, and it was **out of surface, not out of budget**: it requested 643 rows
and realized 312, throwing away 38,268 of 38,580 attempts as duplicates. Its combination space
(2 token/parameter pairs x 12 adjectives x 12 frames) is only ~288 sentences. A family with no
unused combinations cannot grow, and it was sitting at half its quota with 120 downward rows.

**Fix.** Widen the noun surface at the family's unchanged weight: 3 plurals and 3 singulars per
token (`fireballs`/`shots`/`blasts`, `speed boosts`/`boosts`/`sprints`), 10 noun frames and 7 verb
frames instead of 7 and 5. No new family, no weight change, so no other family's quota is
divided -- which is the dilution that cost `more duration and more multiplier` in P20.

| | P23 | P24 |
|---|---|---|
| `param_speed_contrast` realized | 312 | **643** |
| ... downward rows in it | 120 | **194** |
| train rows | 7,492 | 7,823 |
| `param_multi_conflict` / `param_multi_together` | 1,220/580, 1,092/450 | **identical** |

Those two multi families draw from the stream *before* this one, so widening a later family
cannot touch them -- which is what makes the comparison clean.

**Evidence.** Best epoch 21.

| metric | P23 | P24 |
|---|---|---|
| live intent exact | 0.363 | **0.391** |
| live bucket accuracy | 0.882 | **0.888** |
| test intent exact | 0.297 | **0.326** |
| test bucket acc, token ok | 0.870 | **0.880** |
| test `moved` | 0.853 | **0.883** (constant 0.858) |
| test `direction` | 0.657 | **0.699** (constant 0.453) |
| vs keyword baseline, test intent exact | -0.017 | **+0.012** |
| golden bucket accuracy | 0.828 | **0.871** (baseline 0.839) |
| golden `moved` | 0.774 | **0.892** (constant 0.839) |

Two lines crossed for the first time: `moved` beats its always-default constant, and the model
beats the keyword parser on the hard synthetic split instead of merely closing the gap.

**The probe count went down and it does not matter.** Probes 15/19 -> 14/19. `slower fireballs`
was fixed, but `more duration and more multiplier` and `stronger and faster fireballs` were lost --
and both live in families whose rows are byte-identical between P23 and P24, so those flips are
training stochasticity, not data. At n=19 the probe set has a noise band of roughly two phrases.
The aggregates moved by +0.028 and +0.029 over 1,100+ rows, which is not noise. Promoting on the
aggregate while naming the probe regressions is the honest ordering.

**Decision: P24 ships.** `model.pt` = `model_p24.pt`, MD5 `3eb0c428402a9691eddaf206f91985d2`.
Corpus on disk is P24's (7,823 rows, both test hashes unchanged at 6734c287... / a9735b40...,
overlap 0). Service restarted and verified over HTTP: `vocab_words 313`, `slower fireballs` returns
ProjectileSpeed lower, `faster fireballs` higher, `fireball` and `stealth` still move nothing.

**Where the fork landed.** The rebalance branch of the decision rule held: giving a starved
direction more rows moved both the phrase and the aggregate, so the limit there was data. But
`punchy but slow` is still wrong (ProjectileSpeed-lower alone; P23 gave Damage-higher +
ProjectileSpeed-higher), and `strong and fast` still drops Damage, and `quicker cooldowns and a
bigger multiplier` still inverts Cooldown. Those three all need a word bound to a parameter the
sentence never names. That is still the architecture question, and it now has a tighter test case
than it had before.

### P25 - The ablation that explained without winning (Measured)

**Hypothesis (from reading the head, not from a metric).** `IntentModel.attend()` already does the
right thing: the parameter name is the attention query over the phrase's per-token states, so the
result is a phrase summary conditioned on which parameter is being asked about. That is exactly the
machinery binding needs. But `bucket_trunk` was fed

```python
hidden = self.bucket_trunk(torch.cat([phrase, context, token, param], dim=-1))
```

and `phrase` is the **mean-pooled, parameter-agnostic** encoding. So the head could answer "the
sentence says *slower*" from `phrase` without ever consulting which parameter it was being asked
about -- and skip `context` entirely. That shortcut predicts the observed error exactly:
`punchy but slow` moving ProjectileSpeed `lower` rather than Cooldown `higher`.

**Change.** One variable: `--no-pooled-phrase` withholds `phrase` from the bucket trunk, leaving
`[context, token, param]`. Parameters 1,477,639 -> 1,444,871, the difference being one d_model
column (32,768), which confirms the trunk is the only thing that moved. `phrase` still feeds
`presence_logits`, so token presence is untouched by design. Old checkpoints still load because the
flag defaults to `True` and is recorded in the saved config.

**The hypothesis was right.** Binding appeared exactly where predicted, for the first time in any
checkpoint:

| phrase | P24 | P25 |
|---|---|---|
| `punchy but slow` | ProjectileSpeed lower | Damage higher + **Cooldown higher** + ProjectileSpeed lower |
| `more duration and more multiplier` | Duration higher only | Duration max + **Multiplier higher** |

Cooldown `higher` on `punchy but slow` is the label four previous checkpoints never produced. The
mechanism was never missing; a shortcut was starving it.

**And it did not win.** The same phrase now over-fires to three parameters, and precision fell:

| metric (valid) | P24 | P25 |
|---|---|---|
| best exact match | 0.860 | 0.837 |
| bucket accuracy | 0.915 | 0.906 |
| `moved` | 0.943 | 0.938 |
| `direction` | 0.726 | 0.662 |
| probes | 14/19 | 14/19 |

Direction is the casualty, and it is the one that matters most for a small corpus: the pooled
phrase is what supplies general intensity ("maximum", "a bit"), and the parameter-conditioned
attention alone does not carry it as reliably.

**Decision: rejected. `model.pt` stays P24.** The ablation bought binding by spending precision,
and lost on every aggregate. It is recorded rather than promoted because it is not an improvement --
but it is the most useful measurement in the sequence, because it converts "the head cannot bind"
into a precise statement: **binding works when the shortcut is removed; the cost is precision,
because one vector is being asked to do two jobs at once** -- carry the sentence's overall intensity
AND say which parameter a word belongs to.

**Why the split trunk was not tried next.** The obvious follow-up is two trunks: `moved` keeps the
pooled phrase, `direction` gets context-only. But P25 already measures direction-without-phrase at
0.662, so that variant predicts a direction score around 0.66 -- no better than the run it would be
trying to beat. A change whose predicted outcome is a tie is the P21/P22 pattern, which is three
runs of churn. The honest position is that the next lever is data again: P25 proves the model can
learn the binding when forced, so the question is whether the corpus gives `slow`/`slower` enough
contrastive supervision per parameter to spend capacity on, not whether the architecture allows it.

### P26 - The biggest error was not the model's job (Measured)

**What changed the plan.** P25 closed the architecture branch with a negative and left the next lever
named as "data". Instead of spending a corpus cycle on that, the question asked was narrower: on the
set the game actually ships, *what shape* are the failures? Everything since P20 had been judged on
aggregates and a 19-phrase probe set, and the per-row distribution had never been looked at.

**Audit 1 - where the misses are.** `Saved/audit_live_failures.py` on the shipped 3-token set: 327
misses of 537 rows (0.391 exact). Decomposed by kind and family:

| kind | count |
|---|---|
| token set wrong | 218 |
| missing parameter | 77 |
| wrong direction | 36 |
| spurious parameter | 13 |

| family | missed / rows |
|---|---|
| `exclude_all_but` | 73 / 73 (100%) |
| `exclude_without` | 65 / 65 (100%) |
| `param_multi_agree` | 69 / 98 (70%) |
| `request_can_we` | 35 / 65 (54%) |
| `allow_terse` | 29 / 60 (48%) |
| `param_single_superlative` | 39 / 110 (35%) |
| `only_prefix` | 16 / 60 (27%) |

Two families were 138 of the 327 misses -- 42% of all error -- and both were already known to score
**0.000 for the model and 0.000 for the keyword baseline**. Binding was not the biggest problem. It
was the third-largest.

**Audit 2 - is it vocabulary?** The obvious alternative explanation was that the test split uses words
the corpus never contained. `Saved/audit_live_oov.py` built the train+valid word set and counted: hits
contain an unseen word on **8%** of rows, misses on **9%**. Effectively identical. The model was
failing on words it had seen many times, so this was structural, not lexical, and no amount of
corpus vocabulary would have moved it.

**The label rule.** `generate_dataset.py` renders both families the same way:

```python
def fam_exclude_without(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    return f"without {join_natural([s.words[n] for n in excluded])}"
```

The sentence names the abilities to **remove**; the label is the rest of the candidate set. That is
set subtraction. Every forward-reading path -- the model and the substring parser alike -- equips
what the sentence names, which is why both sat at exactly 0.000.

**Change.** New `Tools/intent_model/exclusion.py`, called by `serve.py` *before* the model. It fires
only on an explicit marker (`without`, `except`, `all ... but`, a leading `no`) and only when at least
one candidate word resolves, so `without breaking the game` falls through to the model. `but` needs
the extra guard because `param_multi_conflict` renders `punchy but slow fireballs`, where `but` joins
two adjectives and excludes nothing. It returns token names with every bucket left at `default` -- it
emits no numbers, and `ULoadoutValidator` remains the only thing that writes a spec.

**Result on the shipped set.**

| live 3-token (537 rows) | token-set exact | intent exact | bucket accuracy |
|---|---|---|---|
| keyword baseline | 0.339 | 0.128 | 0.797 |
| model alone (P24) | 0.594 | 0.391 | 0.888 |
| shipped (rule + model) | **0.838** | **0.585** | **0.888** |

The rule fired on 131 rows, got 104 right, and produced **zero false fires** outside the two
families. Note the column that did not move: bucket accuracy is identical, because the rule never
emits a bucket. The gain is 0.194 intent exact bought without touching a single weight -- larger
than every training cycle since P20 combined, which delivered +0.028, then -0.023.

**A false alarm chased rather than reported.** On the full 6-token benchmark the rule scored
**0/312**. That is the shape of a broken rule, so it was debugged instead of explained away: `Blink`
is the deliberate held-out token, so test labels never contain it -- and the exclusion families never
*name* it either, because the generator only gives a word to non-forced candidates. Every 6-token row
therefore differed by exactly one token. Removing Blink from the candidate list and re-running gave
**246/312**.

**Which exposed a real generator defect.** 312 - 246 = 66, and there are exactly 66 exclusion rows
carrying a non-default bucket that nothing in the text expresses:

```
'all abilities but the shoot, bubble and sprint'  ->  Invisibility Cooldown lower
```

The words "cooldown" and "lower" do not appear. No parser can recover those labels, so 246/312 is
100% of what the benchmark permits, and the rule's 79% on the live set is the same ceiling. The fix
is a train-only change to `generate_dataset.py` (the `exclude_*` families should emit all-`default`
buckets), recorded rather than made: the test split is frozen, so a train change cannot move a
number, and the rule answers those rows before the model sees them.

**The lesson, stated as a rule for next time.** Before another training cycle, decompose the error by
*cause* rather than reading one aggregate. Here 42% of the error was a compositional rule that no
amount of training would learn, and it was invisible in the headline because it was averaged in with
binding failures that had already had five cycles spent on them. A rule that is exact -- set
subtraction, unit conversion, table lookup -- belongs outside the weights, where it costs nothing and
cannot be unlearned by the next retrain.

**No rebuild.** This is server-side only. `WishCraftPlayerController` is unchanged and still treats
an empty response as "use the keyword parser"; the exclusion path returns a normal, non-empty intent.


### P27 - The next lever, measured before it was spent (Measured)

**Where P26 left it.** `param_multi_agree` was the worst family the rule could not answer: 69 of 98
live rows missed, and the weakest bucket accuracy of any family at 0.664. Its shape is one sentence
naming two different parameters:

```
shorter cooldowns and heavier fireballs
```

`adj_a` belongs to a *different* parameter from `adj_b`, and the trailing noun is the token. The model
has to hold two adjective-to-parameter bindings at once and attach both to a noun that arrives last.

**The tempting reading.** It looks lexical: the corpus may not contain `heavier` next to
`Damage higher` often enough for the weights to spend capacity on it. P24 was exactly that story and
became one of the strongest cycles of the track, so the same move was the obvious next one.

**Audit 1 - is the pairing there at all?** `Saved/audit_multi_agree_support.py` checks, per row and
per non-default clause, whether *any* training row pairs that parameter and bucket with that
adjective. 187 test rows, 374 non-default clauses, **0 starved**. Every pairing the test split asks
for exists in training. This is not P24: there, a family had been asked for something the corpus
never said. Here the corpus says it.

**Audit 2 - then how much support?** `Saved/audit_adjective_support.py`, training-row counts per
sanctioned `(parameter, bucket, adjective)` triple:

| triple | training rows |
|---|---|
| `Damage higher` / `harder` | 36 |
| `Damage higher` / `stronger` | 29 |
| `Damage higher` / `heavier` | 16 |
| `Damage lower` / `softer` | 11 |
| `ProjectileSpeed lower` / `slower projectiles` | 6 |

The cheap thing to reach for is `CURATED_INTENSITY` in `generate_dataset.py`, the adjective pool these
families draw from.

**The hard constraint that closed it (Source).** `CURATED_INTENSITY` is read by the **test** family
`fam_param_multi_agree`. Widening it changes the adjective pool the frozen test split renders from, so
the split hashes move (currently `6734c287...` and `a9735b40...`) and every number in P23, P24 and P26
stops being comparable. It cannot be touched. The only safe widening is `_bare_adjective`, which has
exactly one caller -- `param_multi_together`, a train-only family -- and that would move the thinnest
pairing from 11 to roughly 25: a redistribution *inside* the training set, not new information.

**Why that is not worth a cycle.** The failing surface is not the adjective, it is binding across a
clause boundary: two parameters, decided by two words, colliding on one noun that arrives last.
Fourteen more `softer` rows does not change what the model has to *do* with a sentence, and the
`speed boost` probe shows the failure is not lexical at all -- `give the speed boost quicker cooldowns
and a bigger multiplier` returns Cooldown `higher`, the direction **inverted**, on adjectives that are
perfectly well supported. P24 earned its cycle by moving a family from out-of-surface to in-surface.
This would not.

**Decision: no retrain. P24 stays shipped.** `model.pt` is still P24, MD5
`3eb0c428402a9691eddaf206f91985d2`, 7,823 train rows, and the corpus on disk still describes it.

**Verification that nothing moved.** `Saved/probe_phrases.py` re-run against the same service: **14 of
19**, identical to P24's run, with the same five misses:

```
stronger and faster fireballs
make the fireball strong and fast
make the fireball punchy but slow
more duration and more multiplier
quicker cooldowns and a bigger multiplier
```

At n=19 the probe's noise band is plus or minus 2, so 14 proves little on its own. What it does show
is that a change which *should* have been a no-op was one.

**Two probe behaviours worth naming (Measured).** `make the fireball strong and fast` returns
`ProjectileSpeed higher` and drops Damage entirely: one clause survives, the other is lost at the
join. `give the speed boost quicker cooldowns and a bigger multiplier` returns Cooldown `higher` where
it should be `lower`, i.e. the other clause's direction. Those are the same defect seen from opposite
sides, and both are binding rather than vocabulary.

**The lesson, as a rule for next time.** A change whose predicted outcome is a tie is churn. P21 and
P22 were both that pattern and both lost ground. Before spending a retrain, count the support the
failing rows actually need. If the pairings already exist and the failure is in how the sentence is
composed, the lever is structural -- how two clauses are joined before the bucket head sees them --
not more rows, and P25 measured one such variant at -0.064 on direction. The honest remaining move is
to leave the weights alone.

**Scratch vs shipped.** The four audits written this session live in `Saved/` as throwaway
measurement tools. Only `exclusion.py` and the `eval_live.py` change were promoted into
`Tools/intent_model/`, because those two sit on the serving and measurement path and the audits do not.


### P28 - The shipped path in the game, and the service that was not there (Measured)

**Why this was the next step.** Every number since P20 was measured by running Python over
`test.jsonl`. The route that actually ships had never been exercised:
`WishCraftPlayerController::SubmitRuleText` POSTs to `IntentServiceURL`
(`http://127.0.0.1:8077/rules`), `UAbilityLoadoutManager::ServerSetIntentJson` hands the reply to
`ULoadoutValidator::ValidateIntentJson`, and only that validator writes a spec. Nothing in the offline
measurement covers the POST, the reply shape surviving validation, or the fallback. The editor log
held **zero** `Intent` lines, so the path had not run at all since the last restart.

**First probe: the feature was silently off.** Phrase one returned:

```
Intent request started: give me all abilities but the fireball
Warning: Intent keyword fallback: model service unavailable
Intent request finished (applied): Rules applied (keyword fallback...)
```

and equipped `Fireball`. For a sentence that asks for the complement, the keyword parser equips what
the sentence *names* -- the 0.128-class answer, arriving as a success message in the UI. **Cause: the
service on 8077 was not running.** Nothing in the C++ was wrong.

**A trap inside the diagnosis.** `SubmitRuleText` collapses three different outcomes into one string:

```cpp
if (!bConnected || !Response.IsValid() || Response->GetResponseCode() != 200)
    ApplyKeywordFallback(RuleText, TEXT("model service unavailable"));
```

So a 422 from a live service, a timeout, and a refused connection all read as "unavailable" in the
log. Only the service's own access log separates them, and that is a different file
(`Saved/intent_service.log`). Worth knowing before this path is debugged again.

**Second defect: the launcher.** The first relaunch used `start ... cmd /k`, which holds a console
open; the process died shortly after answering two requests and `netstat` then showed nothing on 8077.
Relaunching detached -- `start "WishCraft intent" /min cmd /c` with `uvicorn serve:app --host
127.0.0.1 --port 8077` from `Tools/intent_model` -- held: two `/health` calls eight seconds apart,
PID 24700, listening.

**The six phrases, in game, service live.** Driven through `SubmitRuleText` directly rather than the
widget, so the known `RuleTextBox` click quirk could not mask a result.

| # | phrase | equipped | buckets resolved by the validator | verdict |
|---|---|---|---|---|
| 1 | `give me all abilities but the fireball` | Invisibility, SpeedBoost | all `default` (5.0, 8.0, 1.6, 6.0) | right |
| 2 | `without the invisibility` | Fireball, SpeedBoost | all `default` (20, 2500, 1.5, 5, 1.6, 6) | right |
| 3 | `punchy but slow fireballs` | Fireball | Damage `higher` 40, ProjectileSpeed `default`, Cooldown `higher` 3.0 | wrong -- the rule stayed out, but `slow` became Cooldown |
| 4 | `without breaking the game` | unchanged | abstained, then the keyword fallback rejected it | right |
| 5 | `make the fireball strong and fast` | Fireball | Damage `default`, ProjectileSpeed `higher` 5000 | wrong -- Damage dropped at the join |
| 6 | `give the speed boost quicker cooldowns and a bigger multiplier` | SpeedBoost | Duration `default`, Multiplier `default`, Cooldown `higher` 12.0 | wrong -- inverted, Multiplier dropped |

**What this settles.** Three of six, and the three that fail are the three the offline record predicts
-- which is the point. P26's exclusion rule survives `ValidateIntentJson` in the running game: both
negation phrasings produced the complement with every bucket `default`, and the validator filled the
base specs. The abstain guard holds end to end too: `without breaking the game` was refused with a
plain-English message and the loadout was left alone, so the bare-`but` style false positive is not
reachable through the shipped path. And the binding failures reproduce in the game exactly as on
`test.jsonl`, so they are not an artefact of the evaluation harness.

**Observed and unexplained.** Phrase 3 took **5.4 s** (16:31:40.875 to 16:31:46.261); phrases 1, 2, 4,
5 and 6 all answered in 42-61 ms. One sample, no hypothesis worth acting on, but a host-UI hitch of
that size would be visible, so it is recorded rather than dismissed.

**The operational lesson.** The model layer fails *silently by design*: no crash, no dialog, a plain
`applied` message, and one Warning line most players would never see. Starting the service is part of
running the game, and the checkpoint discipline does not cover it. Before any in-game test: `curl -s
localhost:8077/health` and confirm `vocab_words 313`.


### P29 - Two clauses, one rule (Measured)

**The decision that made this short.** P27 had already counted the training support and found it
sufficient, and P21, P22 and P25 had all attacked binding in the weights and lost ground. P27's own
rule -- a change whose predicted outcome is a tie is churn -- therefore ruled out another retrain.
The target moved instead: not "raise `test.jsonl`" but "make the phrases a host actually types
work". Training is frozen at P24.

**Symptom.** `param_multi_agree` is the worst real family: 69 of 98 live rows missed, buckets 0.664.
The three in-game failures from P28 are all this shape:

    punchy but slow fireballs            -> slow became Cooldown, ProjectileSpeed left at default
    make the fireball strong and fast    -> Damage dropped at the join
    quicker cooldowns + bigger multiplier-> direction inverted, Multiplier dropped

**Cause.** Not vocabulary and not a missing pairing: the model cannot hold two
adjective-to-parameter bindings at once and attach both to a noun that arrives last.

**Fix.** `clauses.py`, a deterministic layer that runs before the model exactly as `exclusion.py`
does. It splits the sentence on `and` / `,` / `but`, resolves each clause to one
`(parameter, bucket)`, and answers only when two distinct parameters resolve unambiguously. It
emits token names and bucket labels, never numbers, so `ULoadoutValidator` is untouched.

**Three defects found while building it, each one measured rather than guessed.**

| # | symptom | cause | fix |
|---|---|---|---|
| 1 | rule fired on 21 rows, wrong on 15; 4 of 6 probes never reached it | the family renders `noun_form(name)`, so the corpus says `fireballs`; whole-word alias matching found no token at all | singularise both sides before comparing |
| 2 | `quicker cooldowns and a bigger multiplier` resolved to SpeedBoost | `catalogue.json` lists `fast`, `faster`, `quicker` as SpeedBoost keywords, and 122 SpeedBoost rows vs 164 non-SpeedBoost rows (the `SPEED_ADJECTIVES` measurement) says such a word cannot carry presence | drop every alias that is entirely a direction word before naming the token |
| 3 | `a bigger multiplier` made Fireball look like a viable reading | a clause naming a parameter the token does not have fell through to the bare adjectives and matched Fireball's Damage | refuse the phrase for that token instead of falling through |

Two lexicon errors were found the same way, from the miss list rather than from intuition:
`as long as possible` is Duration **max** but contains `long`, which read as `higher`; and `briefer`
is Duration **lower** in `CURATED_INTENSITY`, not `much_lower`. Multi-word superlatives now have
their own table, checked first and longest-first.

**The token is decided by which parameter set the clauses fit**, not by a keyword. "quicker
cooldowns and a bigger multiplier" names no ability at all; only SpeedBoost has both parameters, and
that is what identifies it. Where an ability *is* named outright, that ability is preferred and no
other is considered.

**Numbers, 537 live rows.**

| metric | keyword | model alone | shipped P26 | shipped P29 |
|---|---|---|---|---|
| intent exact | 0.128 | 0.391 | 0.585 | **0.650** |
| token set exact | 0.339 | 0.594 | 0.823 | **0.847** |
| bucket accuracy | 0.797 | 0.888 | 0.886 | **0.913** |
| bucket acc \| token ok | 0.723 | 0.870 | 0.900 | **0.920** |

Rules fired: exclusion 131 rows, right on 104 (0.794); clauses 46 rows, right on **46 (1.000)**.
Overlap between the two rules: 0 rows. The `param_multi_agree` family, shipped, goes 0.296 -> 0.653;
its tokens 0.724 -> 0.857 and its buckets 0.646 -> 0.819. The weights did not change, so every one
of those points is the rule.

**In game, through `SubmitRuleText` -> `ValidateIntentJson` -> loadout manager.**

| phrase | validator output | P28 output for the same phrase |
|---|---|---|
| `punchy but slow fireballs` | Damage `higher` 40, ProjectileSpeed `lower` 1250, Cooldown `default` 1.5 | Damage `higher` 40, ProjectileSpeed `default`, Cooldown `higher` 3.0 |
| `make the fireball strong and fast` | Damage `higher` 40, ProjectileSpeed `higher` 5000, Cooldown `default` | Damage `default` 20, ProjectileSpeed `higher` 5000 |
| `give the speed boost quicker cooldowns and a bigger multiplier` | Duration `default`, Multiplier `higher` 3.0, Cooldown `lower` 3.0 | Duration `default`, Multiplier `default`, Cooldown `higher` 12.0 |
| `maximum damage and shorter cooldowns fireballs` | Damage `max` 100, ProjectileSpeed `default` 2500, Cooldown `lower` 0.75 | not previously run |

All four finished `applied`. The fourth exists only to prove `max` and `lower` survive the
validator; P28 had only ever exercised `higher` and `default`.

**Tradeoff.** The rule fires on 46 of 98 rows in its own family; the other 52 still go to the model.
Coverage was not pushed further because every extra lexicon word is a precision risk, and 46/46 is
the bar the miss list is holding it to. It is also a third hand-maintained surface table, and it has
an opinion the generator's tables do not (an ordered preference per word, so `fast` on Fireball is
ProjectileSpeed and on SpeedBoost is Multiplier) -- which is why it is its own table rather than
derived from `CURATED_INTENSITY`.

**Observability, open at the time.** Every layer logged the same line: `Rules applied (intent model)`,
including when a rule answered and the model was never consulted, so the editor log could not tell
you which layer produced a loadout. A body field was not available for it -- the response shape is
load-bearing on both sides -- so it was recorded here rather than bundled into this change.
**Resolved in P31**, which carries the answering layer in the `X-Intent-Source` response header; the
log line now reads `Rules applied (intent model: clauses).`

**Discipline held.** No retrain, no regeneration: `model.pt` is still P24, MD5
`3eb0c428402a9691eddaf206f91985d2`, corpus unchanged, both test-split hashes untouched, and
`CURATED_INTENSITY` was read but never widened.


### P30 - The clause rule was refusing on its own dictionary (Measured)

**Symptom.** P29's rule was right on every row it answered -- 46 of 46 -- and that was the problem.
`param_multi_agree` has 98 live rows and the other 52 went to the model, which got 18 of them
(0.346). Coverage, not precision, was what held the family at 0.653.

**Why nobody could see it.** `measure_clauses.py` prints the rows a rule answered *wrongly*, so the
52 rows it never fired on were invisible in every report. The first step here was not a fix but a
scratch script (`_debug_coverage.py`, deleted after use) that printed, for each non-firing row, the
ability names it found and what every clause resolved to, or which guard refused it. Reading that
list is what turned this from guesswork into three named causes.

**Cause and fix, three defects.**

| # | symptom | cause | fix |
|---|---|---|---|
| 1 | `named: []` on every phrase ending in `invisibilities`; the token could not be identified at all | `_singular` stripped only a trailing `s`, so `invisibilities` became `invisibilitie`. The corpus renders `noun_form(name)`, and `pluralise` turns a y-ending display name into `-ies` | map `-ies` to `-y` before the `-s` rule |
| 2 | `longer cooldowns` refused the whole phrase, in about 20 rows | `BARE["longer"]` carried Duration and Range readings only, so a clause that explicitly named `cooldowns` had no reading for Cooldown and refused | add Cooldown **higher** as the third candidate, so Duration and Range still win wherever they exist |
| 3 | `maximum projectile speed and longer cooldowns fireballs` named two abilities and refused | `speed` is a SpeedBoost keyword, so `projectile speed` names SpeedBoost while also being the name of Fireball's parameter | when several are named, fall back to the evidence the unnamed case already uses -- the parameter set the clauses fit -- and refuse unless exactly one survives |

**Then the same computation, on one clause.** `param_single_superlative` is 110 live rows at 0.645
shipped. A phrase with no conjunction at all that names one ability and one parameter is the same
arithmetic the rule already did; `_resolve_for` fills every unmentioned parameter with `default`,
which is exactly what those labels want. It now runs with `min_params=1` when the sentence has one
clause, and 2 otherwise. The lexicon gaps were found the same way, from the printed misses:
`biggest` and `maximum speed` are Multiplier **max**, `barely any speed` is Multiplier
**much_lower**, `more/less of a boost` are Multiplier higher/lower, `forever` is Duration **max**,
and `twice` is **higher** read against whatever parameter the clause named.

**The fix that was measured and rejected.** Gating the single-clause path on an explicit parameter
word looked principled: `invisibility quicker` and `the biggest boost fast` name an ability and a
bare adjective and nothing else. Measured, it avoided 6 wrong rows and took 18 right ones with it --
fired 198/185 became 174/167 and shipped intent exact *fell* 0.750 to 0.734. Reverted, and the two
real causes behind those misses (`biggest`, `barely any speed`) fixed instead. The lesson is P27's,
restated: predict the outcome before shipping, and prefer the fix that is a dictionary entry over
the fix that is a policy.

**Numbers, 537 live rows.**

| metric | keyword | model alone | shipped P26 | shipped P29 | shipped P30 |
|---|---|---|---|---|---|
| intent exact | 0.128 | 0.391 | 0.585 | 0.650 | **0.756** |
| token set exact | 0.339 | 0.594 | 0.823 | 0.847 | **0.881** |
| bucket accuracy | 0.797 | 0.888 | 0.886 | 0.913 | **0.950** |
| bucket acc \| token ok | 0.723 | 0.870 | 0.900 | 0.920 | **0.952** |

Rules fired: exclusion 131 rows, right on 104 (0.794) -- unchanged; clauses **198 rows, right on 188
(0.949)**. Overlap between the two rules: 0 rows. `param_multi_agree` shipped goes 0.653 -> **1.000**
(98/98, every row of the family) and `param_single_superlative` 0.645 -> 0.855. The weights did not
change, so all of it is the rule. Five probes were added for the shapes that only became reachable
here, and all fifteen probes resolve correctly.

**In game, through `SubmitRuleText` -> `ValidateIntentJson` -> loadout manager.**

| phrase | validator output | P29 output for the same phrase |
|---|---|---|
| `maximum projectile speed and longer cooldowns fireballs` | Damage `default` 20, ProjectileSpeed `max` 5000, Cooldown `higher` 3.0 | not reached -- the rule refused it |
| `shoot with the maximum damage` | Damage `max` 100, ProjectileSpeed `default` 2500, Cooldown `default` 1.5 | not reached -- one parameter was not enough |
| `the longest cooldowns and maximum speed speed boosts` | Duration `default` 5.0, Multiplier `max` 3.0, Cooldown `max` 30.0 | not reached -- `maximum speed` was refused |
| `less of a boost and briefer speed boosts` | Duration `lower` 2.5, Multiplier `lower` 1.05, Cooldown `default` 6.0 | not reached -- `less of a boost` was refused |

All four finished `applied` and replicated to the client. One operational detail worth keeping: the
controller serialises in-flight rule requests, so submitting four phrases in one Python call sends
only the first -- they have to go one per call, with the log read between.

**Tradeoff.** The rule is now the largest single producer of labels in the shipped path: 198 of 537
rows never touch the model. Its precision is 0.949 rather than P29's 1.000, and that drop is the
price of the 152 extra rows -- ten wrong answers bought 34 net correct ones. It is still a
hand-maintained surface table, and every word added is a precision risk; the miss list remains the
only legitimate place to extend it.

**The residue, named.** Ten rows the rule answers wrongly. Seven are a single generator template:
`generate_dataset.py:659` renders `make the {p} on {word} lower` for both the `lower` and the
`much_lower` bucket, so a `much_lower` row is word-for-word indistinguishable from a `lower` one and
no rule can separate them. Recorded, not fixed -- the test split is frozen. The other three
(`invisibility quicker`, `invisible quicker`, `hide fast`) want two abilities from a phrase that
names one; the model misses all three too, so firing costs nothing and buys nothing. Related:
`allow_terse` shows 0/3 on the rule for exactly those rows.

**Discipline held.** No retrain, no regeneration: `model.pt` is still P24, MD5
`3eb0c428402a9691eddaf206f91985d2`, corpus unchanged, both test-split hashes untouched, and
`CURATED_INTENSITY` was read but never widened. The rule still emits token names and bucket labels
only, and `ULoadoutValidator` is still the only thing that produces a number.


### P31 - The log line finally names the layer (Measured)

**Symptom.** P29 recorded it and P30 repeated it: every layer produced the same line,
`Rules applied (intent model)`. So the editor log could not tell you whether `exclusion.py`,
`clauses.py` or the P24 checkpoint had answered the phrase you had just typed -- and 198 of the 537
live rows now go through a rule, so that was no longer a corner case. Debugging a bad loadout meant
re-posting the phrase to `/rules` by hand and hoping the answer matched what the game did.

**Why it was not simply fixed.** The obvious change -- add `"source": "clauses"` to the response
body -- breaks rule application outright. Two independent guards require the JSON root to hold
**exactly one** field, and for opposite reasons:

| guard | where | what a second key does |
|---|---|---|
| `Root->Values.Num() == 1` | `WishCraftPlayerController.cpp:204` | the test that distinguishes the empty-list "abstain" sentinel from a real answer stops matching |
| `Root->Values.Num() != 1` | `LoadoutValidator.cpp:144` | rejects the entire response: *"The model returned an invalid rules response. No rules were changed."* |

A provenance field in the payload therefore converts every successful rule application into a
rejection. That is why P29 named the gap and shipped nothing.

**Fix.** The layer travels in a response header, `X-Intent-Source`, so the body stays byte-identical
and neither guard is loosened. Four exits, four values:

```python
# serve.py -- one header line per return, no body change
if not token_names:
    response.headers["X-Intent-Source"] = "no-catalogue"
    return {"tokens": []}
```

`exclusion`, `clauses` and `model` are set the same way on the other three exits. The endpoint
signature had to change to get the object: `def rules(request: RuleRequest, response: Response)`.

The read is inside `OnProcessRequestComplete`, where the response is valid:

```cpp
const FString Source = Response->GetHeader(TEXT("X-Intent-Source"));
...
FString::Printf(TEXT("Rules applied (intent model: %s)."), *Source)
```

The empty-string branch is kept, so an older service build still logs the old line instead of
`(intent model: ).`

**Header or nothing: the two ways to prove it.** HTTP first, because it isolates the Python side
from the game. `Saved/verify_intent_source.py` posts the same body the controller builds -- token
names, display names, category, keywords, and range **names** only, since numeric ranges stay
game-side -- and prints the header next to the returned token names:

| phrase | `X-Intent-Source` | tokens |
|---|---|---|
| `shorter cooldowns and heavier fireballs` | `clauses` | Fireball |
| `shoot with the maximum damage` | `clauses` | Fireball |
| `without the invisibility` | `exclusion` | Fireball, SpeedBoost |
| `make the fireballs punchier` | `clauses` | Fireball |

The fourth row is a probe label that was wrong, not a rule that was: `punchier` is a Damage
adjective on a single-clause phrase naming one ability, which is exactly the P30 `min_params=1`
path. Corrected in the script.

Then in game, one phrase per call:

```
Intent request started: shoot with the maximum damage
Intent resolved Fireball.Damage: max -> 100.000
Intent resolved Fireball.ProjectileSpeed: default -> 2500.000
Intent resolved Fireball.Cooldown: default -> 1.500
Intent request finished (applied): Rules applied (intent model: clauses).
```

**Tradeoff.** Observability is now one header and one log line, and that is all it is: the player
still sees the generic `ClientMessage`, so this is a developer tool, not UI. It also adds a
contract that is invisible to the type system -- a typo in either the header name or a source value
fails silently back to the old generic line, because an absent header is a legal state. Two
compile-and-restart steps are now coupled to any Python edit that touches the exits: uvicorn runs
without `--reload`, so a `serve.py` change needs a service restart, and the C++ change needed Live
Coding. Both were done and both were verified.

**Residue, named.** The patch that inserted the header read also shifted the indentation of the
surrounding block by one tab in places -- the `if` bodies and the statements after them no longer
line up. It is whitespace only, braces pair correctly, and the file compiles; three repair attempts
missed the exact text and were abandoned rather than chased. Worth recording because a reader
opening the lambda will see it and wonder whether something was deleted.

**Discipline held.** `model.pt` is still P24, MD5 `3eb0c428402a9691eddaf206f91985d2`; corpus, both
test-split hashes and `CURATED_INTENSITY` untouched. `clauses.py` and `exclusion.py` were not
modified at all -- they still emit token names plus bucket labels and still return `None` rather
than guess. The `/rules` body was not changed in any way, which is the whole point.




### P31a - The tutorial documented the fix but not the reading (Source)

**Symptom.** P31 shipped the header and wrote it up as three happy-path values: `exclusion`,
`clauses`, `model`. A reader following PART 8.13 could reproduce it, then immediately hit three things
the text did not cover -- the per-entry guard that blocks the next clever idea, what the *other* four
lines in the log mean, and which layer to suspect from the line they were looking at. Put bluntly:
the doc explained the change, not how to use it.

**Cause.** The write-up was scoped to what was edited (`serve.py`, the controller lambda) instead of
to what a reader has to be able to do afterwards. The four `X-Intent-Source` values were listed as
four peers, which is what the code does but not what the game shows -- see the finding below.

**Finding, and it is the useful part.** `no-catalogue` is **unreachable in the game.** `serve.py:132`
stamps the header and returns `{"tokens": []}`; `WishCraftPlayerController.cpp:203-208` tests for the
empty list *before* it reads the header, and calls `ApplyKeywordFallback(RuleText, "model abstained")`.
The header is set, then discarded. So the advertised four values are three values in the log plus one
that only the Python probe can show -- and `model abstained` silently covers two different bugs
(below the 0.8 floor / no catalogue at all), which the game log cannot separate.

**Fix (documentation only, no code changed).**

- `Docs/IntentModelTutorial.md` PART 8.13: Step 2 gained the `LoadoutValidator.cpp:163`
  per-entry guard (`Object->Values.Num() != 2`) next to the two root guards. New **Step 7** gives the
  eight terminal log states -- three layers, `model` present but header absent, three keyword-fallback
  reasons, and a validator rejection -- plus the `no-catalogue` trap, the two-cause ambiguity of
  `model abstained`, and the extra **Warning**-severity `Intent keyword fallback: <reason>` line. New
  **Step 8** maps each log value to where the bug lives, and tabulates all thirteen rejection messages
  the validator can print with their line numbers. Old Steps 7 and 8 became 9 and 10.
- `Tools/intent_model/README.md`: the header section now states the `no-catalogue` consequence and the
  three fallback reasons in the same slot.

**Tradeoff.** The tutorial got about ninety lines longer for a feature that is one header and one
`Printf`. That is the right trade here: the rejection table is only worth having because the guards
are strict, and a reader who does not know all thirteen messages will meet them one at a time.

**Evidence class (Source).** `serve.py:126-176` (four exits, `no-catalogue` returning an empty list);
`WishCraftPlayerController.cpp:189-247` (empty-list test at 203-208, `Source` read at 199, the three
`ApplyKeywordFallback` call sites at 191/206/222, reasons at 229, finished line at 244);
`LoadoutValidator.cpp:34/55/77/89/138/146/150/158/166/186/192/202/207`. Every line number quoted in
the tutorial was read from those files this session. `model.pt` still P24 at MD5
`3eb0c428402a9691eddaf206f91985d2` -- no checkpoint, corpus or rule was touched.



### P32 - Every number in the tutorial is now diffable, not just quoted (Measured)

**Claim being tested.** PART 9 told the reader what to run but never what a correct run prints. So a
reader could follow it exactly, get *some* output, and have no way to tell a faithful reproduction
from a broken one -- the only figures in the document were prose quotations in PART 8 and the README,
with no command that regenerates them side by side.

**Measured.** `eval_live.py`, run against checkpoint `model.pt` MD5
`3eb0c428402a9691eddaf206f91985d2` (P24) at 2026-11-16 14:30:

| block | token set exact | intent exact | bucket accuracy | bucket acc / token ok |
|---|---|---|---|---|
| keyword baseline | 0.339 | 0.128 | 0.797 | 0.723 |
| intent model alone | 0.594 | 0.391 | 0.888 | 0.870 |
| shipped | 0.881 | 0.756 | 0.950 | 0.952 |

Row counts `537` of `1166` kept, `629` dropped; `exclusion` fired on 131 rows, `clauses` on 198. Every
one of those figures is identical to the values already published in PART 8.12 and the README, so the
tutorial's quoted numbers are now backed by a run rather than only by a previous run.

**Why the numbers are assertion-grade.** None of the eight commands in PART 9 samples.
`generate_dataset.py` is seeded, the checkpoint is fixed, and `exclusion.py` / `clauses.py` are
straight-line deterministic. A disagreement therefore has exactly three possible causes -- a
different corpus, a different checkpoint, or an edited rule module -- and the reference block names
which line points at which. This is the property that was missing: it turned a set of values into a
set of checks.

**Fix (documentation only).** `Docs/IntentModelTutorial.md` PART 9 gained
`## The reference run — what a correct reproduction prints`: the full trimmed `eval_live.py` output
with its timestamp and checkpoint hash, a five-row table naming which lines are the assertion and what
a mismatch implicates, the `certutil` hash check, and a note that the by-family block is diagnostic --
specifically that `exclude_all_but` / `exclude_without` reading `0.000` on `mdl exact` while holding
`0.945` / `0.938` bucket accuracy is the *expected* signature of a complement rule, not broken
training. `param_multi_agree` at `0.664` is named as the genuinely weak family so the reader does not
chase the two zeros instead.

**Tradeoff.** A committed output block ages: any future retrain or rule change makes it wrong until
somebody re-runs the command and pastes the new block. That is accepted deliberately -- a stale block
that fails to match is a signal, whereas a prose figure in PART 8.9 silently reads as current state,
which is the exact confusion P31a had to disambiguate. The timestamp and checkpoint hash are there so
a stale block is visibly stale rather than quietly misleading.

**Evidence class (Measured).** `eval_live.py` stdout, this session; `certutil -hashfile
Tools\intent_model\model.pt MD5` -> `3eb0c428402a9691eddaf206f91985d2`, unchanged. No source file, no
rule module, no checkpoint and no corpus row was modified by this pass.



### P33 - The layer name never reached the game at all (Measured, then Source)

**Symptom.** First live PIE run since P31 -- and the first time the header had ever been read by the
game rather than by the Python probe. Sent `anything except invisibility`. The loadout was *right*:
Fireball and SpeedBoost at every default, Invisibility excluded, exactly what `exclusion.py` should
produce. The finished line read:

```
Intent request finished (applied): Rules applied (intent model).
```

No layer. That is the `Source.IsEmpty()` branch -- the one documented as "an older service build, or
a proxy that strips it". Neither applied. `Saved/verify_intent_source.py`, run a minute later against
the same running service on the same port, printed `exclusion | expected exclusion | without the
invisibility -> Fireball, SpeedBoost`. The writer was fine. The reader was not.

**Cause (Source).** `FHttpResponseCommon::GetHeader`
(`Engine/Source/Runtime/Online/HTTP/Private/GenericPlatform/HttpResponseCommon.cpp:76`) resolves a
name with a plain `Headers.Find(HeaderName)` against a `TMap<FString, FString>` -- **case-sensitive**,
no folding anywhere in the path. uvicorn/Starlette emit header names lowercased, so the wire carried
`x-intent-source`. The controller asked for `X-Intent-Source`, got null, and returned an empty
string. No error, no warning, no log -- an absent header and a mis-cased lookup are the same value to
the caller.

**Fix.** `WishCraftPlayerController.cpp` now tries the canonical spelling and falls back to the
lowercase one, with the reason in a comment. Live Coding only; no signature or class change.

**Verified in PIE.** Four phrases, four terminal states, all as documented:

| phrase | finished line |
|---|---|
| `anything except invisibility` | `Rules applied (intent model: exclusion).` |
| `shorter cooldowns and heavier fireballs` | `Rules applied (intent model: clauses).` |
| `make the fireball hurt more` | `Rules applied (intent model: model).` |
| `banana helicopter` | `Rules rejected: I could not find an ability I know in "banana helicopter". Try mentioning: Fireball, Invisibility, SpeedBoost.` |

So all three layers plus the fallback lane are now confirmed end to end in the game, not just from
the probe. The fourth row also settles a doubt recorded earlier: the refusal the player sees is
specific and actionable, not the generic `ClientMessage` that was assumed.

**Tradeoff.** Two lookups instead of a case-insensitive scan of `GetAllHeaders()`. The scan is
strictly more correct against an unusual server; the two-try version covers every real casing and
keeps the hot path to one map probe in the common case. Revisit only if a proxy is ever put in front
of the service.

**Why the probe missed it.** The probe reads the same header from Python's `requests`/`http.client`,
which normalise header names. Nothing in the Python path can reproduce a C++ case-sensitive lookup,
so no amount of probing would have found this. The lesson is narrow and worth keeping: a probe proves
the *service*, never the *client*, and the two disagree exactly where the client is hand-rolled.

**Evidence class (Measured).** PIE session on `/Game/Maps/Lvl_Arena`, four console `RulesModel`
invocations through the possessed `BP_ThirdPersonPlayerController_C_0`; finished lines quoted above
from `Saved/Logs/WishCraft.log`. Cause (`Source`): the engine implementation cited above, read this
session on UE 5.8. `model.pt` still P24 at MD5 `3eb0c428402a9691eddaf206f91985d2`; no rule, corpus or
checkpoint file touched.



### P34 - The player-facing panel is the path that counts, and it works (Measured)

**What was still open.** Every in-game proof so far -- P31, P32, P33 -- went through the `RulesModel`
console command. That is a developer shortcut. A player types into `WBP_RuleInput` and presses Send,
and the widget graph does strictly more than the console does: it casts `GetOwningPlayer()` to
`WishCraftPlayerController`, gates on `HasAuthority && IsLocalController`, binds `OnRuleResolution` so
the status line can report back, and then calls `SubmitRuleText`. None of that was exercised.

**Result.** Typing `shorter cooldowns and heavier fireballs` and pressing Send, in the real panel:

```
Intent request started: shorter cooldowns and heavier fireballs
Intent resolved Fireball.Damage: higher -> 40.000
Intent resolved Fireball.ProjectileSpeed: default -> 2500.000
Intent resolved Fireball.Cooldown: lower -> 0.750
Intent request finished (applied): Rules applied (intent model: clauses).
```

and the panel's `StatusText` reads `Rules applied (intent model: clauses).` in green. Both readings are
right: `heavier fireballs` is 2 x 20 = 40 and `shorter cooldowns` is 0.5 x 1.5 = 0.75. The attribution
now reaches the player, not just the log.

**A false alarm worth recording.** Three attempts to drive the Send button synthetically all failed
the same way: press-down handled (`hit_widget: STextBlock` on the label, `SButton` when clicking off
it), release *unhandled*, no `OnClicked`, no `Intent request started`. That reads exactly like a dead
button, and it was not one -- one real click worked immediately. Synthetic Slate input cannot complete
a press against this HUD, so it is not evidence about the widget. Ruled out along the way: a
hit-testable `SendLabel` swallowing the press (the bare-`SButton` press failed identically), the
authority gate (that branch would have written *"Only the host can edit rules."* into the status
text, and the status text never moved), and the service (probe and console both fine).

**Fix.** None. Documentation only: `Docs/IntentModelTutorial.md` Step 6 gained a subsection that
scripts the panel path, quotes this run, and warns against diagnosing the button from automation;
`Tools/intent_model/README.md`'s attribution bullet now states the panel path is verified too.

**Evidence class (Measured).** PIE on `/Game/Maps/Lvl_Arena`, real mouse click on Send after the text
box was primed; log lines quoted from `Saved/Logs/WishCraft.log` at 02.10.17; the status text was
reported back by the user, who is looking at the panel. No code, rule, corpus or checkpoint changed in
this pass.



### P35 - The exclusion rule was never 0.794. The corpus was (Measured, then Source)

**Symptom.** The previous pass finally printed the exclusion rule's miss list -- it was the only shipped
component without one -- and all 27 misses had the same shape: **the ability set was already right and
only a bucket was wrong.**

```
MISS [exclusion] without projectile, LightningStrike and shield
     got  Invisibility[...all default], SpeedBoost[...all default]
     want Invisibility[...all default], SpeedBoost[Cooldown=higher ...default]
```

Twenty-seven of the 131 wrong intents on the shipped path came from a rule that had never been wrong
about which abilities to equip. That is not a rule defect and reading it as one wastes the next session.

**Cause (Source).** The exclusion renderers build the sentence out of the *excluded* tokens only:

```python
def fam_exclude_without(rng, s):
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    return f"without {join_natural([s.words[n] for n in excluded])}"
```

but `prepare()` has no branch for them, so every `exclude_*` family falls through to
`sample(rng, mentioned_count=rng.choice([0, 1]))`. `mentioned_count=1` picks one **surviving** token and
pins a random bucket on one of its parameters -- and the sentence prints no word that expresses it. The
label asserts something the text does not say, which is exactly the noise `generate_dataset.py` forbids
in its own words, three hundred lines away, where `param_single` narrows `s.tokens = [name]` so that
*"the same words would have to mean two different things"* can never happen.

Measured, with `Saved/audit_exclusion_labels.py`:

| split | exclusion rows | label asserts a silent bucket |
|---|---|---|
| test, live 3-token | 138 | **29** (0.210) |
| test, all families | 312 | 66 (0.212) |
| train | 1414 | 316 (0.223) |

**The plausible hypothesis was wrong, and the miss list is what killed it.** The trailing words in the
failing rows read like intensity words -- `haste`, `turbo`, `quick`, `speedy`, `protect`, `barrier` -- so
the obvious next move was to teach `exclusion.py` to read a bucket off them. They are not intensity
words. Every one of them is an ability alias: `haste`/`turbo`/`quick`/`speedy`/`zoom` name SpeedBoost and
*are* correctly excluded (`without blast, LightningStrike, Shield and speedy` drops SpeedBoost and keeps
Invisibility); `lightning`/`shock`/`bolt`/`blast`/`electrocute`/`LightningStrike` and
`shield`/`guard`/`barrier`/`bubble` name abilities the live 3-token catalogue does not contain, which is
why the rule correctly ignores them, and why they cannot be excluded. `without strike, guard and quick`
labels Invisibility `Duration=lower` -- no reading of `quick` produces that.

**Fix (measurement, not rule).** `measure_clauses.py` gained two functions and now reports every rule
twice -- raw, and on the rows whose text actually determines the label:

* `build_ambiguity()` -- every surface form the corpus maps to more than one label. It also catches the
  second defect class: `generate_dataset.py:659` emits the identical frame
  `make the {param} on {word} lower` for both `lower` and `much_lower`.
* `undetermined(row)` -- why a row cannot be read off its text, or `""`. Two reasons: `label not in text`
  (the exclusion families above) and `same words, two labels` (the `:659` frame).

The miss list annotates each row with its reason and counts the ones no rule change can fix.

| reading | exclusion rule | clause rule |
|---|---|---|
| raw | 104/131 (0.794) | 188/198 (0.949) |
| **text determines the label** | **104/104 (1.000)** | 185/192 (0.964) |
| misses that are corpus noise | 27 of 27 | 3 of 10 |

The same pass prints what that means for the whole split: **40 of the 537 live rows carry a label their
text does not determine, so 0.926 intent exact is the ceiling** -- the most any system, model or rule,
can score on this test set. The shipped path's 0.756 is 0.170 from a wall, not 0.244 from perfection.

**Tradeoff -- and the decision.** `exclusion.py` is **not** changed. A rule that guessed a bucket would be
choosing blind, with 27 rows of hindsight available and 104 correct rows exposed to regression; the
measured position is that the information is not in the sentence. The generator defect is recorded, not
repaired, for the same reason the split is frozen everywhere else: fixing it moves 66 test labels and 316
train labels, which retires every published number including PART 9's reference block. What changed is
that the two can no longer be confused -- a rule's score and a corpus's noise now print separately, and
the 7 remaining clause misses (`make the cooldown on <alias> lower` wanting `much_lower`) are visibly the
`:659` frame rather than a rule that needs work.

**Evidence class (Measured).** `Saved/measure_clauses.log` (header now reads `undetermined : 40` and
`ceiling : 0.926`) and `Saved/audit_exclusion_labels.py`, both this session. `eval_live.py` was not
re-run and its figures did not move, so PART 9's reference block is still current. No code path, rule,
corpus or checkpoint changed; `model.pt` is still P24 at MD5 `3eb0c428402a9691eddaf206f91985d2`.



### P36 - The measured path was three layers. The game runs four. (Measured, then Source)

**Symptom.** Every published intent-model figure since the rules went in described
`exclusion -> clauses -> model`. That is what `serve.py` does. It is not what a player experiences. A
player who types `how about attack` gets Fireball, because the client answers it itself when the
service declines -- yet the measured path records that row as an empty intent and counts it wrong.

**Cause.** The composition has a fourth layer that lives in C++, not in `Tools/intent_model`. Three
call sites in `WishCraftPlayerController.cpp` hand the phrase back to the keyword parser:

| line | trigger | reason string |
|---|---|---|
| 191 | service unreachable or non-200 | `model service unavailable` |
| 214 | exactly one root field `tokens`, array empty | `model abstained` |
| 230 | the request could not be started | `model request could not start` |

Line 214 is the one that matters here: **an empty intent is a signal, not a failure.** `serve.py`'s
module docstring already said so -- "If nothing clears the confidence floor the response is an empty
list, which is the signal to fall back to the keyword parser" -- and the measurement never modelled it.

**How it surfaced.** `measure_clauses.py` had no miss list for the composed path; only the two rules had
one. Adding `diagnose_shipped()` split the 131 wrong intents into two failures that had been read as one
block of work:

| failure | rows | meaning |
|---|---|---|
| wrong token set | 64 | the wrong abilities were named (presence) |
| right set, wrong bucket | 67 | the abilities were found, a level was misread (direction) |

The 64 are almost pure **under-firing**: one invented ability across the whole set. 49 of them are
`request_can_we` (24), `allow_terse` (18) and `only_prefix` (7) -- the families the model never trained
on, because `test.jsonl` splits by whole family. The path was not naming the wrong ability; it was
naming nothing.

**Fix (measurement, not code).** `measure_clauses.py` gained three functions and now reports the service
and the game separately:

* `diagnose_shipped()` -- each miss tagged `SET` (missing / spurious) or `BKT` (truth -> predicted), with
  per-family totals and the undetermined count kept out of the path's charge.
* `fallback_gain()` -- exact, not estimated: a client fallback can only change rows the service returned
  empty, so the question is only how many of those the parser gets exactly right.
* `apply_client_fallback()` -- substitutes `evaluate.baseline_intent` (the port of
  `URuleParserLibrary::ParseRuleText`) on every empty intent, producing the as-played path.

The port was verified against the C++ before being trusted: `Normalize` lowercases and strips space,
hyphen and underscore; matching is `Contains` on the token name then each keyword; `ParseRuleText`
iterates the live `Table->Tokens`, which is the same 3-token set as `catalogue_live.json`. It
`AddUnique`s and then sorts, where the port appends in catalogue order -- irrelevant, because every
comparison in the scoring is set-based.

| path | token set | intent exact | bucket |
|---|---|---|---|
| keyword baseline | 0.339 | 0.128 | 0.797 |
| model alone | 0.594 | 0.391 | 0.888 |
| service (exclusion + clauses + model) | 0.881 | 0.756 | 0.950 |
| **as played (service, then client keyword fallback)** | **0.909** | **0.771** | **0.950** |

40 of 537 rows reach the client empty. The parser is exactly right on **8** of them and wrong on **0** --
and it cannot be otherwise, since the fallback only fires where the service produced nothing. The 8 are
`request_can_we` 4, `only_prefix` 2, `allow_terse` 1, `category_verb` 1.

**Tradeoff -- and the decision not to touch `serve.py`.** A server-side fallback was the obvious move
and would have been wrong twice over. It duplicates a layer the client already runs, and it *consumes
the signal*: the client would stop logging `model abstained`, which is the only evidence that the
confidence floor is what silenced a phrase. The +0.015 is not a gain to be won; it is a correction to
the record. Every earlier headline understated the game by 0.015 intent exact and 0.028 token set, and
the honest statement is that the service figure is the one a change to this directory can move.

One nuance recorded rather than modelled: `ParseRuleText` returns false with "I could not find an
ability I know" when nothing matches, so `ApplyKeywordFallback` applies nothing and the previous
loadout persists. Scored as an empty intent, which is a miss either way -- the difference is invisible
to intent-exact and only matters to a player who expected their old loadout to stay.

**Open, recorded not fixed.** `undetermined()` only inspects `exclude_*` families, but the same
generator fallthrough that pins a random bucket on a surviving token also produces rows in
`only_prefix`, `allow_terse` and `request_can_we` that assert a bucket the sentence never expresses --
`only cloaked` labelled `Invisibility.Cooldown much_lower`, `faster` labelled
`SpeedBoost.Multiplier default`. Those are charged to the path today. The ceiling is therefore still
an **under**-count of the impossible rows, not an over-count. (Closed in P37: the count was 40, it is
76, and the ceiling is 0.858 -- see below.)

**Evidence class (Measured, then Source).** `Saved/measure_clauses.log` this session -- the
`keyword parser as last resort` block and the `-- as played` block, both from one run at exit 0.
Source: `Source/WishCraft/WishCraftPlayerController.cpp:191,214,230` and
`Source/WishCraft/Rules/RuleParserLibrary.cpp:38-67`. `model.pt` untouched at P24, MD5
`3eb0c428402a9691eddaf206f91985d2`; no rule, corpus or checkpoint changed; the service was left
running and healthy (`/health` returned `vocab_words 313`, `confidence_floor 0.8`).



### P37 - The ceiling was an under-count: 40 impossible rows was 76 (Measured, then Source)

**Symptom.** P36 closed with an open item: `undetermined()` only inspected families starting with
`exclude_`, yet the miss list was printing rows from other families that were just as impossible and
still charged to the path --

```text
BKT  only cloaked            (only_prefix)   Invisibility.Cooldown much_lower -> default
BKT  faster                  (allow_terse)   SpeedBoost.Multiplier default -> higher
```

`only cloaked` names one ability and says nothing about a cooldown. `faster` is labelled **all-default**
and the model answered `higher`: the corpus is wrong and the model was right. So the published ceiling
of 0.926 was an under-count of the rows no system can answer -- and the whole 0.756-vs-0.926 framing
was resting on it.

**Cause -- a naming heuristic standing in for a mechanism.** The check was

```python
if row.get("family", "").startswith("exclude_"):
```

which is a claim about family *names*. The defect is structural. `prepare()` has a branch for ten family
groups (`ABSTAIN_FAMILIES`, `category_only`/`category_verb`, `only_suffix`, `param_single_max`,
`param_single*`, `param_multi_conflict`, `param_multi_agree`, `param_multi_together`,
`param_speed_contrast`, `equip_only`) and everything else reaches its last line, `:1248`:

```python
return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
```

`sample():512` is what that means: one random `(token, parameter)` pair is marked "driven", and
`sample_buckets():479` gives every driven parameter a weighted random bucket from `BUCKETS` -- `default`
with weight 0.55 -- leaving the rest at `default`. So half the rows in those families assert one
arbitrary bucket, and `default` is drawn four times in five of those. All ten renderers build their
sentence from `s.words[n]` plus fixed frame text (`:535-593`, `:934-943`), so none of them can print the
intensity word that would justify the bucket.

**Fix (measurement).** `UNEXPRESSED_BUCKET_FAMILIES` is now a literal frozenset of the ten names, and
`undetermined()` tests membership instead of a prefix. `only_suffix` is deliberately **absent**:
`prepare():1059` narrows it to `mentioned_count=0` and rewrites every bucket to `default`, so its labels
are honest -- a test on `startswith("only")` would have declared 60 correct rows impossible. The
constant carries the line numbers it was read from, so adding a branch in `generate_dataset.py` visibly
has to shrink it.

**The check that it is one mechanism, not a family quirk.** The rate should fall out of
`BUCKET_WEIGHTS` rather than be observed and accepted:

| measurement | value |
|---|---|
| predicted: `0.5 x (1 - 0.55)` | 0.225 |
| measured, all 323 live rows in fallthrough families | 66/323 = **0.204** |
| measured earlier, exclusion rows only | 29/138 = **0.210** |

Three numbers clustering on one arithmetic. A mechanism-specific quirk would not produce that.

**Consequence.**

| | before | after |
|---|---|---|
| undetermined live rows | 40 | **76** (66 `label not in text`, 10 `same words, two labels`) |
| ceiling on the split | 0.926 | **0.858** |
| composed path, determinate rows | not measured | **399/461 (0.866)** service, **407/461 (0.883)** as played |

The exclusion rule and the clause rule are unchanged at **104/104** and **185/192** on determinate rows,
which is the proof that this moved *labels* and not judgements -- exactly the property to demand of a
measurement change. The headline 0.771 now reads as **54 wrong of 461 answerable rows**, not as 91 short
of a wall that was in the wrong place.

**Tradeoff.** No behaviour changed: `exclusion.py`, `clauses.py`, the corpus and the checkpoint are all
untouched, and `model.pt` is still P24 at MD5 `3eb0c428402a9691eddaf206f91985d2`. The correction makes
the path look better where there is an answer (0.866) and the corpus look worse (14.2% impossible rather
than 7.4%); both are the honest reading. The residual risk is the ten-family list drifting out of step
with `prepare()` -- recorded in the constant itself rather than in a doc, because the code is where it
will be edited.

Every document quoting 0.926 was corrected: `Docs/IntentModelTutorial.md` (Step 11's ceiling block, new
Step 13, PART 10's invariant) and `Tools/intent_model/README.md` (`Defect 2b`).

**Evidence class (Measured, then Source).** `Saved/measure_clauses.log` this session: header reads
`undetermined : 76` with the per-reason breakdown, `ceiling : 0.858`, and the tail prints
`service text determines the label: 399/461 (0.866)` and `as played ... 407/461 (0.883)`. Source:
`generate_dataset.py:47-53` (`BUCKET_WEIGHTS`), `:479-528` (`sample_buckets`/`sample`), `:535-593` and
`:934-943` (the ten renderers), `:1028-1248` (`prepare`).


### P38 - The answer key was the defect: corpus rebuilt, 0.879 shipped (Measured)

**Symptom.** P37 closed with the honest reading of the live split: 54 wrong of 461 answerable rows, under a
ceiling of 0.858 that said a sixth of the split could not be answered by any system. That is a research
result, not a shippable one. The question that forced this phase was blunt -- *if there is something that
can be broken, players will break it* -- and the honest answer at P37 was that the model was not the thing
holding the number down. Three defects, all of them in the generator that writes the labels, were.

**Cause 1 -- the bucketless families asserted a bucket.** P37 named the mechanism for the `exclude_*` rows
and encoded it as a ten-family list. The same fallthrough was serving *every* family with no `prepare()`
branch, at `generate_dataset.py:1248`:

```python
return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
```

`mentioned_count=1` routes into `sample_buckets():479`, which pins a weighted bucket on one arbitrary
parameter -- `default` at 0.55 -- and none of their renderers print an intensity word, only aliases and
frame text. Measured on corpus v1: 66 live rows, 0.204, against a predicted `0.5 x (1 - 0.55) = 0.225`.

**Cause 2 -- one sentence, two labels.** `_param_single_sentence`'s else-branch served `lower` **and**
`much_lower` with identical wording, at `:659` (curated) and `:668` (train). `make the damage on the
fireball much lower` and `make the damage on the fireball lower` were the same sentence carrying two
different labels.

**Cause 3 -- a frame table that ignored its own bucket.** `fam_param_multi_together` emitted the same
frames for `higher`/`higher` and `max`/`max`. Nothing in `both ...` says which of the two is meant, so the
surface form carried two labels.

**Fix -- make the corpus auditable, then gate the write.** New `Tools/intent_model/dataset_audit.py`
(stdlib only, so the generator can import it without torch) reports the two defect classes a sentence
cannot express: a row in a bucketless family asserting a non-default bucket, and one surface form carrying
two labels across `train` / `valid` / `test` (`test_heldout` excluded, mirroring
`measure_clauses.build_ambiguity`). It runs standalone for a baseline *and* inside `generate_dataset.main()`
as a **pre-write gate**: all four `write_jsonl` calls were moved after it, so a failing corpus writes nothing.

Baseline on corpus v1 -- the numbers that made this a rebuild and not a label patch:

| | count |
|---|---|
| `label not in text` | 918 |
| `same words, two labels` | 57 |
| undetermined, train / valid / test / heldout | 660/7,823 · 84/600 · 194/1,166 · 58/289 |

The first regeneration was **blocked by the gate** on three `param_multi_together` collisions and wrote
nothing at all -- which is the gate doing the job a printed report would not have done.

**Fix -- plus one defect the audit could not see.** The clause rule was still wrong after the regen: 12 rows
of `make the {p} on {word} much lower` were answered `lower`, because `clauses.py` resolved the two-word
intensifier through its single-word lexicon and kept the last word. `GENERIC_MULTIWORD`, checked *before*
the lexicon, fixes it:

```python
GENERIC_MULTIWORD = [
    (("much", "lower"), "much_lower"),
    (("much", "less"), "much_lower"),
    (("a", "great", "deal"), "much_lower"),
]
```

`param_single_superlative` goes from 12 wrong to **101/101** on the clause rule. That defect was in the
*reader*, not the corpus, and the audit is blind to it by construction -- it compares the corpus with
itself.

**Measurement, corpus v2** (7,823 / 600 / 1,166 / 293 = 9,882 rows, `--seed 20261108`):

| | undetermined | ceiling |
|---|---|---|
| corpus v1 | 76 live rows | 0.858 |
| corpus v2 | **0** | **1.000** |

P38 is best epoch 13 of 30 (valid `exact` 0.812), MD5 `541a625039746380baa8015c77bf2220`. P24's
`3eb0c428402a9691eddaf206f91985d2` is history only. The live 3-token set is 587 of 1,166 rows:

| path | token set | intent exact | bucket | bkt\|tok |
|---|---|---|---|---|
| keyword baseline | 0.325 | 0.147 | 0.838 | 0.744 |
| model alone (P38) | 0.608 | 0.373 | 0.901 | 0.855 |
| service | 0.894 | **0.879** | 0.989 | 0.994 |
| as played | **0.913** | **0.889** | 0.989 | 0.991 |

Corpus v1 comparators, same code: as played 0.909 / 0.771 / 0.950, service 0.881 / 0.756 / 0.950. Clause
rule 204 fired / 203 right (0.995), the single miss being `invisibility quicker`; exclusion rule 147/147 on
live rows; 0 rows fired by both rules. The 24 rows the path declines still gain +0.010 from the client
parser (`request_can_we` 4, `category_verb` 2), which is exactly the 0.879 -> 0.889 gap.

The other two heads now clear their constants on the full six-token `test.jsonl`: keyword 0.660 / 0.407 /
0.890, model 0.548 / 0.340 / 0.928, **`moved` 0.924 against the 0.890 always-`default` constant** and
**`direction` 0.740 against 0.447**. On `golden.jsonl`, `moved` 0.892 against 0.839 and `direction` 0.867
against 0.467. Held-out `Blink` detection 0.222 at 0.151 false-positive, up from 0.152 / 0.136.

**What is still wrong, stated plainly.** 62 of the 587 live rows name the wrong token set, and most are one
collision: `faster` / `quicker` / `fast` present against `SpeedBoost` absent -- the `faster` ambiguity
P20/P21 documented and never resolved in the corpus, where `faster` alone is labelled
`SpeedBoost.Multiplier default` while the model answers `higher`. Bucket errors are down to 9
(`param_single_superlative` 5, `allow_terse` 4). 24 rows are declined rather than answered, and the client
parser recovers 6 of them. None of this is a hidden defect; it is the residual, and it is now the whole
residual rather than the visible top of a larger one.

**Tradeoff.** Every historical number is retired. This phase changed the answer key, the wording that feeds
it, the checkpoint and all four measurements at once, so no v1-vs-v2 delta isolates a single cause and this
entry does not claim one. The comparison that survives is *within* v2 and *within* one path. One number
worth keeping visible: on the full six-token split the model alone reads 0.548 intent against the keyword
baseline's 0.660. The checkpoint is not a better keyword matcher, and the shipped 0.879 is the model plus
two deterministic rules plus a client fallback -- which is what the game actually runs.

**Evidence class (Measured).** `Saved/dataset_audit_v1.log` (918 / 57, per-split breakdown),
`Saved/regen_v2.log` (`audit problems : 0`, `train/test text overlap : 0`, `wrote 9882 rows`),
`Saved/dataset_audit_v2.log` (0/0/0/0, PASS), `Saved/train.log` (`best epoch 13`),
`Saved/measure_clauses.log` (`undetermined : 0`, `ceiling : 1.000`, `service ... 516/587 (0.879)`,
`as played ... 522/587 (0.889)`, per-family table, probes). Source: `dataset_audit.py` (whole file),
`generate_dataset.py:1248`, `:659`, `:668`, `clauses.py` (`GENERIC_MULTIWORD`), `measure_clauses.py` (the
`BUCKETLESS_FAMILIES` import kept as a canary).

**Verified in game (PIE).** All five phrases were typed into `WBP_RuleInput` and sent in one session with
the service at `127.0.0.1:8077` running throughout, each confirmed twice -- against the loadout the
manager mirrors from the GameState, and against the game's own log:

| phrase | applied |
|---|---|
| `give me the fireball` | Fireball only: Damage 20 / ProjectileSpeed 2500 / Cooldown 1.5 -- every unnamed parameter at its default |
| `only the invisibility` | Invisibility only: Duration 5 / Cooldown 8 |
| `give me everything except the fireball` | Invisibility + SpeedBoost, Fireball absent, all defaults |
| `make the damage on the fireball much lower` | Damage **`much_lower` -> 5.000** (the minimum of the Damage range), ProjectileSpeed 2500 and Cooldown 1.5 unchanged |
| `make me a pizza` | `model abstained`, no keyword matched, loadout unchanged, `Intent request finished (unchanged)` |

The fourth row is the fix proved end to end: it is the phrase the corpus could not express before this
phase. One thing the run made explicit that the code only implies: **the abstain path is a fallback
keyword parse, not a hard no-op.** The controller logs `Intent keyword fallback: model abstained` and then
still runs `URuleParserLibrary::ParseRuleText`, so an abstained phrase that happens to contain an ability
keyword would apply that keyword's match instead of leaving the loadout alone. None of these five phrases
does -- `make me a pizza` matched nothing -- but that is what "abstain" means at the client, and it is
worth knowing before reading a surprising result as a model failure.

---

### P39 - A word that was doing two jobs, and the defect that was in the vocabulary (Measured)

**Symptom.** P38 closed with 62 of the 587 live rows naming the wrong token set, and P38's own entry named
the cause without fixing it: "most are one collision: `faster` / `quicker` / `fast` present against
`SpeedBoost` absent". A player-facing symptom made it concrete -- `make the projectile speed on ball of
fire much lower` returned Fireball *and* a spurious SpeedBoost.

**The measurement that localised it.** In the corpus on disk, 1,495 of 7,788 training rows contain a speed
word. They split 820 SpeedBoost / 675 not. `faster` alone appears in 122 SpeedBoost rows and 164 rows
where SpeedBoost is absent. That is not a model that is unsure; that is a word with two jobs: `faster
fireballs` uses it as a **ProjectileSpeed adjective** (the label moves ProjectileSpeed `higher`), and
`sneak, faster` uses it as **SpeedBoost's name** (the label equips SpeedBoost with everything `default`).
Two readings, one spelling, roughly even split. No amount of training separates them.

**The change, and it is six lines.** `pick_words` chose each ability's name from its alias pool, and that
pool contains the catalogue keywords -- including the speed words. It now filters `SPEED_ADJECTIVE_WORDS`
(`fast`, `faster`, `quick`, `quicker`, `rapid`, `slow`, `slower`) out of the *name* slot, with a fallback
to the unfiltered pool so a token can never become unnameable. `_equip_word` has filtered the same set
since P23; `pick_words` was the last name slot that did not. The adjective survives where it belongs, in
adjective slots, where `fam_param_speed_contrast` makes the noun decide whether the ability is present at
all. SpeedBoost keeps ten clean names: `speed`, `speedy`, `boost`, `haste`, `hasty`, `sprint`, `running`,
`zoom`, `velocity`, `turbo`.

**Corpus v3** regenerated clean: `audit problems : 0`, `train/test text overlap : 0`, `wrote 9882 rows`, the
same 7,823 / 600 / 1,166 / 293 split. The same two pre-existing surface-exhausted families stayed
surface-exhausted (`category_only` wanted 193 got 14, `category_verb` wanted 47 got 12). Retrain at the
defaults: best epoch 16 of 30, valid `exact` 0.840 against P38's 0.812, 1,478,919 parameters (one more
vocab word), MD5 `8866045eb4ad4014523a7437d6d51b6a`.

| live 3-token (588 rows) | P38 / floor 0.80 | P39 / floor 0.80 | P39 / floor 0.50 |
|---|---|---|---|
| model alone, token set | 0.608 | 0.643 | 0.660 |
| service, token set | 0.894 | 0.918 | 0.929 |
| service, intent exact | 0.879 | **0.901** | **0.912** |
| as played, token set | 0.913 | 0.930 | 0.939 |
| as played, intent exact | 0.889 | **0.913** | **0.922** |
| wrong token sets | 62 | 48 | 42 |
| right set, wrong bucket | 9 | 10 | 10 |

The family that proves the mechanism is `param_single_superlative`, which is where the adjective and the
name collided: **9 wrong token sets -> 1**. `allow_terse` fell 22 -> 13 for the same reason (its terse
renders are where `speed`/`faster` used to arrive as a second ability). Two families moved the wrong way --
`exclude_without` 3 -> 6 and `exclude_all_but` 1 -> 4 -- and that is a measurement artefact, not a
regression: see the residual below.

**P40 - the floor nobody had swept.** With the corpus clean, the last free knob was the presence floor.
It had been 0.8 since the first checkpoint, on no argument but convention, and the 0.8-based comment in
`evaluate.py` said so out loud ("0.8, not 0.5, because that is what `serve.py` ships"). It had never been
tested. `Saved/sweep_floor.py` caches nothing and simply re-decodes at each floor, and also scores 200
abstain rows from `train.jsonl` -- the only source of "the right answer is nothing" -- as a
false-positive control:

| floor | token set | intent | invented tokens | declined rows | control FP |
|---|---|---|---|---|---|
| 0.80 | 0.643 | 0.401 | 157 | 66 | 0.00 |
| 0.70 | 0.646 | 0.405 | 164 | 59 | 0.00 |
| 0.60 | 0.650 | 0.410 | 170 | 55 | 0.00 |
| **0.50** | **0.660** | **0.415** | 173 | 51 | 0.00 |
| 0.40 | 0.662 | 0.417 | 176 | 49 | 0.00 |
| 0.30 | 0.663 | 0.418 | 179 | 48 | 0.00 |

The curve is **flat from 0.80 down to about 0.50** and then stops paying: 0.50 buys +0.017 token-set /
+0.014 intent for +16 invented tokens, and 0.50 -> 0.30 adds +0.003 for +6 more. Control false positives
stay at 0.00 at every floor, which says the model's nonsense probabilities are far below 0.3 and that the
floor was never doing the abstention work -- the training objective was. 0.50 is the knee, so 0.50 is what
ships (`INTENT_CONFIDENCE`, `serve.py:64`), and `measure_clauses.py`, `evaluate.py` and `eval_live.py` were
all moved to it so no report describes a configuration nothing runs. On the deployed path that is worth
0.901 -> 0.912 service, 0.913 -> 0.922 as played.

On the **six-token stress set** the same move is a wash, and saying so is the honest half: token set 0.611 ->
0.607, intent 0.407 -> 0.408, held-out `Blink` detection 0.041 -> 0.072 but control false positives 0.063 ->
0.087. The looser floor buys detection of the unseen token and pays for it in false positives. That trade is
correct only because the six-token set is not what ships -- **a confidence floor quoted without its
candidate set means nothing.**

One number did get worse and is recorded rather than hidden: `golden.jsonl` direction 0.867 -> 0.667 at the
new floor. That is 2 of 15 moved labels, golden is 20 hand-written phrases never used for selection, and it
is not a signal. It is written down so the next session does not have to rediscover it and mistake it for
one.

**What is still wrong, stated plainly.** 42 of the 588 live rows name the wrong token set and 10 name the
right set with the wrong bucket -- 52 misses, 0.922 as played. The token-set residual:

| family | rows | what is wrong |
|---|---|---|
| `request_can_we` | 13 | names present in the sentence, absent from the answer -- never an invented token |
| `allow_terse` | 11 | same: `ball of fire, ghost` loses Invisibility |
| `exclude_without` | 6 | **artefact**, see below |
| `only_prefix` | 5 | `only attack, ghost and speed` loses two names |
| `exclude_all_but` | 4 | **artefact**, see below |
| `param_single_superlative` | 2 | spurious SpeedBoost on an adjective that is now clean in training |
| `category_verb` | 1 | `can we go all mobility` -- the client parser fixes this one |

Ten of those are the artefact: `without lightning and barrier`, `all abilities but the shock and barrier`.
The **generator's candidate pool includes the two `planned` tokens** (`LightningStrike`, `Shield`), so an
exclusion family can render a sentence excluding abilities that are not in the live set -- and the label is
then "all three", correctly, but no three-token system can resolve `lightning`. `measure_clauses.py` drops
rows whose *label* names an unshippable token (588 of 1,166 kept) but not rows whose *sentence* does. Those
six-plus-four rows are therefore reported as model misses when they are a corpus/game scope mismatch. The
fix is a decision, not a code change: either restrict the generator's candidates to live tokens (which
would collapse the `category_*` families, since only one live token per category exists) or drop those rows
from the live measurement the way their labels are already dropped. Neither is done here.

**Tradeoff.** The `pick_words` filter changes the alias pools, so corpus v3 row *content* differs from v2
even where row counts are identical: this is not a clean A/B and the table above should be read within one
column, not across two. P38's checkpoint was overwritten by the retrain -- there is no backup -- so P38 can
only be reproduced by reverting the six-line filter and retraining. `faster`'s *bucket* question (a row
labelled `SpeedBoost.Multiplier default` that the model answered `higher`) is untouched by a name-slot
filter and remains open.

**The transferable lesson.** A defect in the *vocabulary* presents exactly like a defect in the *model*:
62 rows wrong, no bias visible in any single prediction, and the natural response is to train longer or
widen the architecture. The way to tell them apart is to count how many corpus rows use the word in each
of its two senses. That count took minutes and the fix took six lines. And the floor sweep is the mirror
image of it: a constant that has a comment explaining why it is right but no measurement behind it is not a
decision, it is a habit -- 30 seconds of re-decoding found +0.011 of shipped accuracy sitting there.

**Evidence class (Measured).** `Saved/regen_v3.log` (`audit problems : 0`, `wrote 9882 rows`),
`Saved/train.log` (`best epoch 16`, `parameters 1,478,919`), `Saved/sweep_floor.log` (the full 0.30-0.80
table), `Saved/measure_clauses.log` (P39 at floor 0.50: `undetermined : 0`, `ceiling : 1.000`,
`exclusion ... 143/143`, `clauses ... 210/210`, `service ... 536/588 (0.912)`, `as played ... 542/588
(0.922)`, per-family table, 42 wrong sets with every row printed), `Saved/measure_p39_floor080.log` (the
same checkpoint at the retired floor), `Saved/evaluate.log` (full six-token split), `Saved/eval_report.json`.
Source: `generate_dataset.py:504` (`pick_words`), `:203` (`SPEED_ADJECTIVE_WORDS`), `serve.py:64`,
`measure_clauses.py:43`, `evaluate.py:441`, `eval_live.py:37`, `Saved/sweep_floor.py`.

**Not verified in game at the time of writing.** The editor was closed for the whole phase, so the
shipped-path numbers above are `measure_clauses.py`'s composition of the same four layers the controller
runs, not a controller run. **P40 is the pass that closed that gap**, and closing it cost two defects --
neither of them in the model.

### P40 - Proven in the client, and the rule module the corpus could not fail (Measured, then Source)

The P39/P40 numbers were a re-decode. This entry is the pass that ran the same four layers through the
controller and read the log, and it is worth keeping because **both defects it found were invisible to
every measurement in the document above**, one of them in the build and one in a rule module certified at
143/143.

**1. The missing feature was a missing build.** The first pass applied every loadout correctly and every
line read `Rules applied (intent model).` -- the row P31's table calls *the header did not arrive*.
Read literally that says the service is older than P31. The service was correct:

| file | timestamp |
|---|---|
| `Binaries/Win64/UnrealEditor-WishCraft.dll` | 11/8/2026 11:25:22 PM |
| `Source/WishCraft/WishCraftPlayerController.cpp` | 11/16/2026 2:40:42 AM |
| `Binaries/Win64/UnrealEditor-WishCraft.patch_0.exe` | 11/16/2026 2:41 AM |
| the DLL after `rebuild_and_restart_editor` | 11/17/2026 2:36:44 AM, 1,170,944 bytes |

The C++ was eight days newer than the DLL. Live Coding had applied the fix once, into a process that has
since exited -- **a patch module never writes the DLL**, so a clean launch starts from the old binary and
no amount of re-patching changes that. After the rebuild the suffix resolved for all three layers:
`nuke stealth sprint` and `attack, unseen, speed` -> `(intent model: model)`; `shorter cooldowns and
heavier fireballs` -> `(intent model: clauses)`; `give me a fireball without the speed boost` ->
`(intent model: exclusion)`; and `banana milkshake` -> `Warning: Intent keyword fallback: model
abstained`, then the parser matched nothing and `ULoadoutValidator` refused with the loadout untouched.
Four layers, no dead ones.

**2. The exclusion rule had a hole in exactly one direction.** The phrase at the end of that list is the
one that broke: it equipped **Invisibility**, the only token the sentence never names.

```text
give me a fireball without the speed boost   ->   Invisibility        (complement of {SpeedBoost})
```

`apply_exclusion` subtracted *every* candidate the sentence named, so
`{Fireball, Invisibility, SpeedBoost} - {Fireball, SpeedBoost}` left the unmentioned token. It could not
tell an excluded name from a requested one. `fam_exclude_without` only ever renders `without <the
excluded names>` -- there is no template with a wanted name before the marker -- so **143/143 was 100%
of a happy path.** The fix (`exclusion.py:92`) splits the candidate list at the marker:

```python
def _split_at_marker(words, marker):
    try:
        index = words.index(marker)
    except ValueError:
        return words, []
    return words[:index], words[index + 1:]
```

`kept` is what is named before the marker, `excluded` what is named after; `kept - excluded` when `kept`
is non-empty, the old complement only when it is not. `Saved/probe_exclusion_sides.py`: nine
corpus-shaped rows **byte-identical**, and three player-shaped rows fixed -- `give me a fireball without
the speed boost` Invisibility -> Fireball, `let me vanish without speed` Fireball -> Invisibility,
`give me a fireball except invisibility` SpeedBoost -> Fireball.

**3. The shipped vocabulary was not the trained one.** The DataAsset held 5 / 5 / 6 keywords per ability;
`catalogue.json` held 12 / 13 / 15. Training reads the catalogue, so this was invisible to every number
in P39 -- but `build_catalogue` lets the **request** override, and the client sends the DataAsset's list.
`Saved/measure_served_keywords.py` at floor 0.50 over the 588 live rows:

| keywords from | token-set F1 | intent exact | empty answers |
|---|---|---|---|
| `catalogue.json` (what training saw) | 0.693 | 0.660 | 42 |
| the old DataAsset (what serving saw) | 0.694 | 0.658 | 51 |
| the aligned DataAsset | 0.693 | 0.660 | 42 |

A wash on the corpus -- 0.001 of F1 and nine uncounted abstentions -- but not in the rules: `vanish` was
not a keyword in the asset, so `build_aliases` could not resolve it and `let me vanish without speed`
fell through the exclusion layer to the old complement. The asset was aligned to the catalogue's naming
surface minus `faster` / `quick` / `quicker`, which stay withheld on purpose: they are the comparative
adjectives P39 removed from the name slot, and putting them back as client aliases re-creates the defect
the client parser enforces rather than detects.

**4. The floor, proven in the client that ships.** `Saved/run_service_floor080.bat` pinned
`INTENT_CONFIDENCE=0.80` for the A/B, then `Saved/run_service.bat` restored 0.50:

| phrase | at 0.50 | at 0.80 |
|---|---|---|
| `nuke stealth sprint` | `(intent model: model)` -> Fireball, Invisibility | `(keyword fallback: model abstained)` -> Fireball, Invisibility, SpeedBoost |
| `fireball, invisible, hasty` | `(intent model: model)` -> Fireball, SpeedBoost | `(keyword fallback: model abstained)` -> Fireball, Invisibility, SpeedBoost |

The 0.80 column is the **keyword parser**, and it equips every name it can see. Raising the floor does not
make the path safer, it hands the phrase to a dumber layer -- which is why vocabulary parity matters more
than the floor does.

**The band moved, and that had to be re-measured.** Enriching the keywords raised scores enough that two
band phrases stopped discriminating: `attack, unseen, speed` 0.709 and `mobility only please` 0.771 are
both above 0.80 now. The replacement band, measured after alignment: `shoot Invisibility haste` 0.721 ->
SpeedBoost, Fireball; `fireball, invisible, hasty` 0.740 -> Fireball, SpeedBoost; `only ball of fire,
stealthy and zoom` 0.767 -> SpeedBoost; `nuke stealth sprint` 0.771 -> Fireball, Invisibility.

**No regression from the rule fix.** `measure_clauses.py` after `_split_at_marker` reproduced every P39
number: exclusion 143/143 (`exclude_without` 73/73, `exclude_all_but` 70/70), clauses 210/210, service
0.929 / 0.912 / 0.994 / 0.993, as played 0.939 / **0.922**, declined 18, parser fixes 6 (+0.010),
`EXIT 0`. A rule fix that leaves every corpus number untouched is exactly right when the corpus cannot
express the broken shape.

**Tradeoff.** The asset edit is a gameplay change dressed as a config tidy: it changes what the client
parser matches *and* what the server resolves, because they read one list. And the two files are now kept
in agreement by a script rather than by the build -- `Saved/check_keyword_parity.py`, which checks
`asset == catalogue - banned adjectives` and reports `PARITY OK -- 3 live tokens match, 3 banned
adjective(s) correctly absent`. Its first version compared the lists for equality and so failed on every
run; a guard that always fails is a guard nobody reads, which is why the contract and not the equality
is what got written down.

**The transferable lesson.** A corpus certifies the shapes its generator can render, not the behaviour of
the code. Both of this pass's defects were products of that gap -- `exclude_without` could not render a
wanted name before the marker, and nothing in the corpus path could see the client's keyword list at all.
The cheap check is not another measurement; it is **running the shipped artifact and reading what it
says**, after comparing its timestamp with its source's.

**Evidence class (Measured, then Source).** `Saved/Logs/WishCraft.log` (the four layer lines and the
rejection), `Saved/probe_exclusion_sides.log` (nine identical, three fixed), `Saved/measure_served_keywords.log`
(0.693 / 0.660 / 42 both sides), `Saved/probe_pie_floor.log` and `Saved/probe_floor_band_served.log` (the
A/B and the re-probed band), `Saved/measure_clauses.log` (P39 numbers reproduced), plus `Saved/check_keyword_parity.py`
(`PARITY OK`). Source: `exclusion.py:92` (`_split_at_marker`), `serve.py` (`build_catalogue`,
`CONFIDENCE` at `:64`), `WishCraftPlayerController.cpp` (`SubmitRuleText` at `:124`), `Docs/IntentModelTutorial.md`
PART 8.16.

### P41 - The ten rows nothing in here could answer, and why the fix was the measurement (Measured, then Source)

The P39/P40 residual was 52 of 588, and ten of those were never misses. `fam_exclude_without` and
`fam_exclude_all_but` draw their excluded names from the full six-token candidate pool, so on the
live subset they render sentences like `without lightning and shield` against a label that is all
three live tokens. `apply_exclusion` can resolve neither name, finds nothing to subtract, returns
`None`, the model abstains, and the empty token set scores as three misses. The row passes the live
filter because its *label* is live-only; nothing looked at its *sentence*.

**The fix had to be narrow, and the first one was not.** Flagging every row that merely names a
planned token caught **153 of the 153** live exclusion rows. Naming one in the excluded position is
harmless and common: `without lightning and fireball` resolves on `fireball`, subtracts it, and that
is exactly the label -- because the shipped candidate set never carried `LightningStrike` in the
first place. The artefact is only a sentence whose exclusion has **nothing live to work with**. With
that criterion the set is **10 rows, 6 `exclude_without` + 4 `exclude_all_but`** -- the number the
P39 residual had predicted, which is the check that the criterion is the right one.

| reading | rows | token set | intent |
|---|---|---|---|
| service, full live split | 588 | 0.929 | 0.912 |
| as played, full live split | 588 | 0.939 | **0.922** |
| service, expressible only | 578 | 0.945 | 0.927 |
| as played, expressible only | 578 | 0.955 | **0.938** |

**Why the measurement and not the corpus.** The other candidate fix -- restrict the generator's
candidate pool to the live tokens -- re-renders every row in all four splits, moves both test-split
MD5s, and voids every number published before it (P38 and P39 both did exactly that, and their
checkpoints cannot be diffed against each other). Ten rows that no rule, model or checkpoint change
could answer are not worth that. The corpus stayed frozen; `model.pt` and both test hashes are
unchanged, and the full-588 figures above still reproduce byte for byte.

**Nothing regressed.** `measure_clauses.py` after the change: exclusion 143/143 (`exclude_without`
73/73, `exclude_all_but` 70/70), clauses 210/210, service 0.929 / 0.912 / 0.994, as played 0.939 /
0.922, `undetermined` 0, `ceiling` 1.000, overlap 0, `EXIT 0`.

**Tradeoff.** The expressible subset is a denominator change, and a denominator that moves is a
number that can flatter. It is reported *below* the full split rather than replacing it, and the
selection rule is a property of the sentence (`exclusion_marker` present, no named token live), not
of the outcome -- nothing is dropped because the system got it wrong.

**Evidence class (Measured, then Source).** `Saved/measure_clauses_p41.log` (both blocks, `EXIT 0`),
`Saved/measure_clauses_check.log` (the P40 reproduction taken before any edit). Source:
`exclusion.py` `mentioned`, `measure_clauses.py` `unshippable`.

### P42 - The vocabulary the game sends was never the one being measured (Measured, then Source)

The PIE run that closed P41 contradicted the probe that preceded it. `nuke stealth sprint` returned
**Fireball + Invisibility** in the game; the same phrase through `Saved/probe_layers_p41.py` returned
**Invisibility only**. That is not a client bug, it is two different keyword lists:

```text
catalogue.json       Fireball keywords include "nuke"
catalogue_live.json  Fireball keywords do not -- 5 / 5 / 6 keywords per token
```

5 / 5 / 6 is the count P40 replaced in the DataAsset. **`catalogue_live.json` is the vocabulary P40
retired**, and it is the file every live figure in this document is measured against, because
`serve.build_catalogue` lets the request's lists override and the client sends the DataAsset.

**The fix is to build the served lists, not to keep a copy of them.** `served_catalogue()` derives
them from `catalogue.json` in code -- live tokens, full keyword lists, `BANNED_ALIASES` withheld --
which is the contract `Saved/check_keyword_parity.py` already enforced against the asset. Checked
before quoting anything: `/Game/Sandbox/DA_AbilityTokens` is `MATCH` on all three tokens (12 / 13 /
12 keywords, 0 mismatches, no non-live rows). A fourth hand-maintained copy is what drifted; there
is now no copy.

| reading | rows | legacy `catalogue_live.json` | served (ships) |
|---|---|---|---|
| keyword baseline | 588 | 0.355 | **0.617** |
| model alone | 588 | 0.660 | 0.660 |
| service | 588 | 0.929 / 0.912 | 0.929 / **0.913** |
| as played | 588 | 0.939 / 0.922 | 0.935 / **0.920** |
| as played, expressible | 578 | 0.955 / 0.938 | 0.952 / **0.936** |

The correction is 0.002, and the direction is the interesting part: the keyword parser gets **74%
better** (0.355 -> 0.617) and the composed path gets slightly **worse**. Richer keywords do not
help the game, because the client fallback equips every name it can see -- so a fallback that
matches more names over-equips more often. The composed path is bounded by how often the model
abstains, not by how far the keywords reach. That is P40's floor measurement again, from the other
side.

**Nothing else moved.** On the served lists: exclusion 143/143 (`exclude_without` 73/73,
`exclude_all_but` 70/70), clauses 210/210, `undetermined` 0, `ceiling` 1.000, `EXIT 0`. Both blocks
print, so every P39/P40/P41 figure still reproduces on its own vocabulary.

**Residual on the served vocabulary: 51 of 588** (42 wrong token set + 9 wrong bucket), against 52
on the legacy lists. Ten of the 42 are the unanswerable `exclude` rows from P41, so the real figure
is **41 of 578**. The composition moved more than the total did: `request_can_we` 13 -> 9 (the
enriched keywords let the client parser answer more abstentions) and `param_single_superlative`
2 -> 4, because a richer list gives the model more ways to mistake the *parameter* phrase
`projectile speed` for the *ability* SpeedBoost -- four rows now invent a token instead of missing
one. `allow_terse` is now the largest family at 12.

The PIE run cross-checks the block: the measurement predicts `nuke stealth sprint` -> Fireball +
Invisibility (missing SpeedBoost), and the game produced exactly Fireball + Invisibility. Before
P42 the same row read as missing Fireball *and* SpeedBoost.

**Tradeoff.** The report is now two vocabulary blocks and a reader can quote the flattering one.
The labels are explicit -- `live 3-token` is legacy, `served` is the shipped configuration -- and
`served` is the only one measured against a file that has been verified equal to the DataAsset.

**Evidence class (Measured, then Source).** `Saved/measure_clauses_p42.log` (both blocks, `EXIT 0`),
the PIE run that produced `nuke stealth sprint -> Fireball, Invisibility`, the DataAsset comparison
(12 / 13 / 12, `mismatches: 0`). Source: `measure_clauses.py` `served_catalogue`,
`Saved/check_keyword_parity.py` `BANNED_ALIASES`.

### P43 - A phrase-level rule that was written inside one branch of the naming test (Measured, then Source)

The last rows producing a *wrong* answer rather than a missing one were four
`param_single_superlative` sentences inventing **SpeedBoost**:

```text
make the projectile speed on ball of fire much lower   -> SpeedBoost
let me have twice the projectile speed on projectile    -> SpeedBoost
```

Fireball has a `ProjectileSpeed` parameter, so `speed` is both half of a parameter name and one of
SpeedBoost's keywords. `_named_tokens` therefore returns **two** candidates, and
`apply_clauses` fell into its ambiguous branch. That branch called
`_resolve_for(text, catalogue, name)` with the default `min_params=2` -- while the branch above it
correctly computed `single` from `CLAUSE_SPLIT` and asked for **one** parameter on a conjunction-free
phrase. A one-clause sentence was being asked to resolve two parameters, neither candidate managed
it, the rule refused, and the row went to the model, which answered SpeedBoost.

The rule was about the *phrase*, not about which branch of the naming test ran, so it now sits above
both: `single = len(CLAUSE_SPLIT.split(text)) == 1`, `min_params = 1 if single else 2`. The ambiguous
branch still requires exactly one surviving candidate, so the precision guard is untouched.

| reading | before | after |
|---|---|---|
| clause rule fired | 210 (210/210) | **221 (221/221)** |
| `param_single_superlative` | 102/102 | **113/113** |
| served residual | 51 of 588 | **45 of 588** |
| served as played, intent | 0.920 | **0.930** |
| served expressible, intent | 0.936 | **0.946** |
| served wrong buckets | 9 | **7** |

**Nothing regressed.** Exclusion still 143/143 (`exclude_without` 73/73, `exclude_all_but`
70/70), `param_multi_agree` 108/108, `undetermined` 0, `EXIT 0`. `param_single_superlative`
disappears from the residual entirely: the clause rule now answers all 113 of its live rows.

**What is left.** 45 of 588, of which 10 are the unanswerable `exclude` rows from P41, so **35 of
578** are real: `allow_terse` 12, `request_can_we` 9, `only_prefix` 6, `category_verb` 1. Every one
of those is a *missing* name in a terse list, except a single invention -- `unseen` alone answering
SpeedBoost. The residual is now entirely a presence problem in the model's own territory.

**The transferable lesson.** P42's vocabulary fix created these four rows: a richer keyword list gave
`speed` a second owner. The defect was not in the new vocabulary, it was a rule that had been
correct in the only branch that exercised it. When a change adds candidates, check the code paths
that were never reached before it.

**Evidence class (Measured, then Source).** `Saved/measure_clauses_p44.log` (both blocks, `EXIT 0`).
Source: `clauses.py` `apply_clauses` (the hoisted `single` / `min_params`).

### P44 - The loadout readout: making the model's answer visible to a camera

The intent path was working and unshowable. `WBP_RuleInput` reports only the sentence returned to
`FinishRuleRequest` ("Rules applied (intent model: clauses)."), and every derived number -- the
ability list and the clamped parameter values -- existed only in `UAbilityLoadoutManager::Loadout`
and the log. A recording of the feature therefore proved nothing beyond "a text box accepted a
sentence".

**No C++ was needed.** `AWishCraftPlayerController::GetLoadoutManager()` is already `BlueprintPure`
and both `FOnRuleResolution` (bool, FString) and `FOnLoadoutMessage` (FString) are already
`BlueprintAssignable`, so the entire readout is reachable from UMG. `FAbilitySpec` exposes `Token`
and `Params`; `FAbilityParam` exposes `ParamName` and `Value` -- all `BlueprintReadOnly`.

**`/Game/Sandbox/UI/WBP_LoadoutReadout`** -- top-right panel, deliberately copying `WBP_RuleInput`'s
brush (`TintColor` 0.012/0.016/0.024, `RoundedBox`, radius 14, outline white at 0.12) so the two
read as one HUD. Three text blocks only:

| block | source |
|---|---|
| `Title` | static "Active Rules" |
| `LayerText` | the raw `OnRuleResolution` message -- it already names the layer, so no parsing |
| `LoadoutText` | one line per `FAbilitySpec`: `Fireball - Damage 40 . Cooldown 0.75` |

`RefreshLoadout` walks `Loadout`, formats each `Value` through `ToText(float)` with
`MaximumFractionalDigits = 2` (a raw float would print `40.000000`), joins params with ` . ` and
abilities with newlines, and falls back to "no abilities equipped". `Construct` caches the cast
controller and manager, binds both delegates, then refreshes once so the panel is correct before the
first rule is typed; `Destruct` unbinds and nulls the cache. `WBP_RuleInput`'s existing `Construct`
creates the readout behind an `IsInViewport` guard, because Construct can run more than once.

**Two sizing lessons, both caught by rendering rather than by reading the tree.** `bAutoSize=True`
shrank the border to 389 px and would have re-flowed it mid-take as the text changed -- the width is
pinned instead. A fixed 180 px height then clipped the third ability as soon as two lines wrapped;
the box is 560 x 232, which holds three abilities at two wrapped lines each with room to spare.

**Evidence class (Rendered, then Source).** `Saved/Screenshots/AuraScreenshots/widget_render_WBP_LoadoutReadout_1920x1080_20261119_171603_*.png`
(first pass, autosized), `..._172900_*.png` (clipping at 180 px), `..._172918_*.png` (560 x 232,
three abilities, nothing clipped). Source: `WishCraftPlayerController.h` (`GetLoadoutManager`,
`OnRuleResolution`), `AbilityLoadoutManager.h` (`Loadout`, `OnLoadoutMessage`), `AbilityTypes.h`
(`FAbilitySpec`, `FAbilityParam`).

#### P44a - The spawn node was spliced into a false branch (Verified, then Source)

The first build never showed the panel. A hand-built copy of the widget worked perfectly, which is
what narrowed it: the fault was in the spawn, not in the readout. Three faults, all in
`WBP_RuleInput`'s `Construct`, all invisible to a compile:

1. The `Create Widget` node had been appended inside the existing `Branch(bRuleRequestPending) == True`.
   `bRuleRequestPending` is `false` at BeginPlay, so the create path was dead code on every run.
2. The once-only guard called `Is In Viewport` on `CreatedReadout` **before anything had assigned
   it**. That is a function call on a null widget: `Blueprint Runtime Error: Accessed None trying to
   read (real) property CreatedReadout ... Node: Branch`, and execution stops there.
3. Nothing assigned the created widget into `CreatedReadout` and nothing called `AddToViewport` at
   all, so even a successful create would have been discarded.

The repair moves the create onto the main `Construct` chain after the existing binding logic, guards
on `Is Valid(CreatedReadout)` (a value test, not a call on the value), then `Create -> Set ->
AddToViewport(0)`. `RefreshLoadout` also gained a `Trim Trailing`, because the joined string ended
in a newline and left an empty line under the last ability.

**Runtime strings after the fix** (`runs/20261119-174239/trace2.jsonl`): at spawn, one copy, `waiting
for a rule` / `no abilities equipped`; after `shorter cooldowns and heavier fireballs`, `Rules applied
(intent model: clauses).` / `Fireball - Damage 40 . ProjectileSpeed 2,500 . Cooldown 0.75`; after
`give me a fireball without the speed boost`, `Rules applied (intent model: exclusion).` / `Fireball -
Damage 20 . ProjectileSpeed 2,500 . Cooldown 1.5` (Fireball only). One copy throughout, no
`Accessed None`, no game errors.

**Two things worth remembering.** `ToText(float)` applies locale grouping, so 2500 renders `2,500`
while 40 and 0.75 do not change -- correct, and not worth fighting. And a widget-spawning node is the
worst place to trust a compile: `Construct` is shared with pre-existing branch logic, so a new exec
chain can be spliced into a path that never runs at BeginPlay without any error being raised. Only a
PIE run that counts live instances catches it. The failing run is `runs/20261119-173106` (0 copies
despite two applied rules).

**Evidence class (Verified, then Source).** `runs/20261119-173106` (fail: 0 live
`WBP_LoadoutReadout`, `Accessed None` on `CreatedReadout`), `runs/20261119-174239` (pass: 1 copy,
both phrases correct). Source: `WBP_RuleInput` `Construct` exec chain.

### P45 - A third-party camera rig, wired in as a camera mode (Verified, then Source)

The framing camera is not project content. It is a plugin: `Plugins/SharedCameraSystem/`,
`SharedCameraSystem.uplugin` (`"Type": "Runtime"`, `EngineVersion 5.8.0`), one module. Two classes
matter: `ASharedCameraManager : APawn` (the actor that owns the framing maths) and
`USharedCameraComponent : UActorComponent` (the registration tag a character wears).

**The component is not opt-in, and that shaped the whole integration.**
`USharedCameraComponent::BeginPlay` runs `GetActorOfClass(ASharedCameraManager::StaticClass())`, sets
`PlayerControllerComp->bAutoManageActiveCameraTarget = false`, calls
`ClientSetViewTarget(SharedCameraManager)`, then `SharedCameraManager->SetPlayer(GetOwner())`. So
wearing the component *is* joining the camera. A "camera mode" therefore is not a flag inside the
camera -- it is whose transform the `PlayerController` currently targets.

**Registration is what makes the framing work, not attachment.** `ASharedCameraManager::SetPlayer`
routes through `ServerSetPlayer` when `!HasAuthority()` and then `Players.Push(Player)`. `Tick` only
calls `CalculateVal()` when `Players.Num() >= 2`, and only `Players[0]` and `Players[1]` are ever
read. `SetCameraPosition` normalises distance by dividing by **1200**, so the pair has to be within a
few hundred centimetres for a tight two-shot.

**Player side, in `AWishCraftCharacter`.** `ToggleCameraAction` (`UInputAction*`), `ToggleSharedCamera()`
and `bUsingSharedCamera` were added; `WishCraft.Build.cs` gained the private dependency
`"SharedCameraSystem"`. The binding sits behind `if (ToggleCameraAction)` with `ETriggerEvent::Started`,
matching every other action in `SetupPlayerInputComponent`. `ToggleSharedCamera` deliberately does not
trust the flag -- it derives the live mode from
`PlayerController->GetViewTarget() == SharedCamera` and then calls
`SetViewTargetWithBlend(NewViewTarget, 0.35f, VTBlend_Cubic)`, logging `Camera mode -> shared|follow`.
A bool that can desync from the actual view target is the more common bug, not the rarer one.

**Input and placement.** `IA_ToggleCameraMode` (Digital, `InputTriggerPressed`) is mapped to **F3** in
`IMC_Default`. `BP_ThirdPersonCharacter` gets `ToggleCameraAction`; the child manager
`/Game/Camera/BP_SharedCameraManager_Child` is placed at `(0,0,0)` and does the registration itself
in `BeginPlay`: `Delay 0.2 -> Get All Actors Of Class(Character) -> Add Shared Camera Component`.
The loop covers Characters only -- `ACombatDummy` is an `AActor`, not a `Character` -- which is why a
stand-in dummy needed a hand-added component at first. It does not any more; see P45b.

**Evidence class (Verified, then Source).** `runs/20261119-184053/trace1.jsonl`: `Players` = exactly
two entries (`BP_CombatDummy_C_0`, `BP_ThirdPersonCharacter_C_0`) at spawn, after a settle, and
through both toggles; F3 -> `BP_ThirdPersonCharacter_C_0` (`bUsingSharedCamera` false), F3 ->
`BP_SharedCameraManager_Child_C_1` (true); no Blueprint runtime errors. Source:
`SharedCameraManager.h`, `SharedCameraComponent.h`, `WishCraftCharacter.h/.cpp`, `WishCraft.Build.cs`.

#### P45a - Two components on one pawn is three players in the array (Verified, then Source)

The first PIE run reported `Players` = `[BP_CombatDummy_C_0, BP_ThirdPersonCharacter_C_0,
BP_ThirdPersonCharacter_C_0]`. The toggle worked, so this looked cosmetic. It was not: the pawn was
carrying **two** components -- `SharedCameraComponent` (added by hand to the Blueprint) and
`SharedCameraComponent_0` (added by the manager child's `BeginPlay` loop). Two registrations, two
`Players` entries, one pawn. It was inert only because `CalculateVal` reads indices 0 and 1 and the
duplicate landed at 2 -- one ordering change away from a degenerate pair.

One owner per registration source is the rule: the Blueprint must not add what `BeginPlay` adds. The
fix is to delete the Simple Construction Script node, leaving the runtime-added component as the only
one. Through Python that is `SubobjectDataSubsystem.k2_gather_subobject_data_for_blueprint`, pick the
handle whose `get_variable_name` matches, `is_component` is true and `is_inherited_component` is
false, then `delete_subobject(context_handle, subobject_to_delete, bp_context)`. **Three arguments,
and the first is the blueprint's root handle, not the blueprint asset** -- the two-argument shape
`(handle, blueprint)` fails with `Cannot nativize 'Blueprint' as 'SubobjectToDelete'`. Delete, compile
(`BS_UP_TO_DATE`), save.

**Evidence class (Verified, then Source).** `runs/20261119-183135/trace2.jsonl` (3 entries, duplicate
at index 2), `runs/20261119-184053/trace1.jsonl` (2 entries, both toggles correct). Source:
`BP_ThirdPersonCharacter` subobject list before and after deletion.

#### P45b - The second participant was a stand-in, and a stand-in is not a player (Verified, then Source)

Asked who the second participant should be, the answer taken was "use the existing combat dummy".
That was a testing convenience, not a design, and it should have been labelled as one: a dummy is a
second *actor*, and the system's job is to frame a second *player*. The dead giveaway was in the
array itself -- the dummy needed a component **added by hand**, while every real player gets one
automatically from the manager's `BeginPlay` loop, precisely because players are Characters.

Removed: the `SharedCameraComponent` deleted from `BP_CombatDummy`, and the
`CombatDummy_CameraPartner` actor destroyed in `Lvl_Arena`. Zero dummies remain in the level.

A real second player needs no wiring at all -- the loop registers every Character -- but two things
were still wrong for a two-player session:

1. **Registration was not authority-only.** Every machine's manager `BeginPlay` adds components to
   its own copies of the characters, and each of those components called `SetPlayer`. On a client
   that call is `!HasAuthority()`, so it also fired `ServerSetPlayer` -- the server registered the
   same character a second time. `Players` is `DOREPLIFETIME`d, so the server's list is the source of
   truth and only the server needs to push into it. `USharedCameraComponent::BeginPlay` now guards the
   registration with `GetOwner()->HasAuthority()`; the local view-target hijack stays unguarded,
   because every machine needs its own controller redirected.
2. **PIE only spawns one player by default.** Play dropdown -> Number of Players = 2, Net Mode =
   Play As Listen Server. `ULevelEditorPlaySettings` is not exposed to Python in this engine build,
   so this one is a manual editor setting, not something a script can do.

With a single player nothing computes: `Tick` calls `CalculateVal()` only when `Players.Num() >= 2`,
so the camera rests at the manager's placed transform. That is expected, not a fault -- the framing
maths has no pair to frame.

**Evidence class (Verified, then Source).** `runs/20261119-190027/trace1.jsonl`: single-player run
with the dummy gone and the guard live -- `Players` = `[BP_ThirdPersonCharacter_C_0]` (1 entry),
view target `BP_SharedCameraManager_Child_C_1`, F3 -> character -> manager, 0 errors, 0 Blueprint
runtime errors, `dummy_actor_count` 0 across 8 samples. Source: `SharedCameraComponent.cpp`
`BeginPlay`, `SharedCameraManager.cpp` `SetPlayer` / `GetLifetimeReplicatedProps`.

### P46 - Deleting the unused template variants, and what actually costs build time (Verified, then Source)

The template ships three variant games. Only one was wanted, so `Variant_Platforming` and
`Variant_SideScrolling` came out entirely and `Variant_Combat` stayed. The stated reason was build
speed, and that reason is worth correcting first: **content does not slow a C++ build.** Compilation
is driven by the `.cpp` files in the module, so the 30 deleted assets and their external actor
packages changed the cook, not the compile. What shortens a build is removing the *classes*.

Before deleting anything, reference-check. The Asset Registry answers this in one pass:
`get_assets_by_path("/Game", recursive=True)`, then `get_dependencies(package, opts)` per asset with
`include_soft_package_references` and `include_hard_package_references` set, looking for edges that
land inside the folders about to go. Result here: exactly one outside asset depended on the template
group -- `Lvl_Arena`, and only for `Blockouts` materials. No live asset referenced a variant. Config
was clean too (`Platforming|SideScrolling` matched nothing in `Config/`), and the only `#include`s of
those modules were the modules' own `.cpp` files. That is the go-ahead.

Deletion, in order: content via `EditorAssetLibrary.delete_directory(path)` -- **one argument in this
engine build**; the `(path, show_confirmation, force_delete)` shape raises `delete_directory() takes
at most 1 argument (3 given)`. The external-actor and external-object packages live in *sibling*
roots (`/Game/__ExternalActors__/Variant_Platforming`, not under the map folder), so each map costs
three paths, and the Asset Registry reports 0 assets in those external roots even while the files are
on disk -- confirm with `os.walk` on `unreal.Paths.project_content_dir()`, not with the registry.
Then `WishCraft.Build.cs` loses its seven include paths, then the two `Source/WishCraft/Variant_*`
folders are removed from disk.

**Deleting a `UCLASS` is not hot-patchable.** Live Coding patches function bodies; it cannot unload a
type. Asking for a rebuild with those modules already gone crashed the editor on its exit path --
`EXCEPTION_ACCESS_VIOLATION reading address 0x0000008000000070`, pathway `Core > WishCraft_patch_1 >
CoreUObject`, no Aura frame, with three patch modules loaded; the diagnosis called it
`LIVECODING_COULDNT_PATCH` (HIGH confidence) and the remedy it wanted was the incremental build that
had been in flight. An incremental rebuild with the editor down compiled clean, and the relaunched
editor loaded with the classes absent.

**Evidence class (Verified, then Source).** Asset counts after deletion: `Variant_Platforming` 0,
`Variant_SideScrolling` 0, `Variant_Combat` 31, `ThirdPerson` 5, `Characters` 128, `Input` 14. Disk
check via `os.walk`: all six removed paths report 0 files. `unreal.load_class` on
`/Script/WishCraft.PlatformingCharacter`, `PlatformingGameMode`, `SideScrollingCharacter`,
`SideScrollingGameMode`, `SideScrollingCameraManager` all return null; `CombatGameMode`,
`CombatPlayerController` and `WishCraftCharacter` still load; `BP_ThirdPersonCharacter`,
`BP_ThirdPersonGameMode`, `BP_ThirdPersonPlayerController` and `BP_CombatDummy` all still resolve a
`generated_class`. Source: `WishCraft.Build.cs` include paths, `Saved/Crashes/UECC-Windows-29097A684EDB9C5FEC9D5DA154AEA7DE_0000`.

### P47 - The component moves onto the class, and two things that aimed at the wrong place (Verified, then Source)

The manager used to discover players: `ReceiveBeginPlay` waited a second, listed every Character and
called `AddComponentByClass(SharedCameraComponent)`. That is a runtime answer to a design question --
which characters carry the framing camera -- and it had two costs. It ran once, so a late joiner was
never registered; and it added a component to actors that already had one, the duplicate registration
P45a chased down.

The class owns it now. `BP_CombatCharacter` is a Blueprint child of `BP_ThirdPersonCharacter` with
`SharedCameraComponent` on the class, and it is the GameMode's `DefaultPawnClass`. The manager's
BeginPlay chain (Delay -> GetAllActorsOfClass -> For Each Loop -> AddComponentByClass) is deleted, so
`ReceiveBeginPlay` goes straight to the parent call. Registration now happens in the component's own
`BeginPlay`, which means a respawn registers itself, and nothing has to sweep the level.

Movement followed the wrong rotation. `DoMove` built its basis from
`GetController()->GetControlRotation()`, and while the framing camera is the view target the control
rotation is not what the player sees -- the manager pawn is. `DoMove` now reads
`GetPlayerViewPoint` when the manager owns the view, which also picks up the blended rotation during
the 0.35 s transition instead of snapping. `DoLook` returns early in that mode: the framing camera is
aimed at the midpoint of two players and is not the caster's to rotate. The test is the live view
target, not `bUsingSharedCamera`, which defaults to true and is never corrected when no manager is
found.

Two more things pointed at the camera instead of the character:

1. **`Players` was never pruned.** `RespawnPlayer` does `UnPossess()` then `DeadPawn->Destroy()` and
   spawns a replacement, so the dead actor stayed at `Players[0]` and the new one appended behind it.
   `CalculateVal` frames indices 0 and 1 -- the two *oldest*, which after the first kill are corpses.
   Once GC clears those slots, `Players[0]->GetActorLocation()` is a null dereference in `Tick`.
   `ASharedCameraManager::Tick` now drops invalid entries before the size check.
2. **The fireball spawned at the camera.** `Ability_Fireball` launched from
   `GetPlayerViewPoint()` + 80 cm, which is correct for a follow camera and wrong under the framing
   camera, where that point floats between the two players. It now spawns at the caster's chest
   (`GetActorLocation` + 60% of capsule half-height) and aims from there. While the shared camera owns
   the view it also aims at the opposing player, because the camera's own aim is the midpoint the
   framing maths picked -- a shot along it flies between both fighters. That is an aim lock at spawn
   time, not a homing projectile.

**Evidence class (Verified, then Source).** GameMode CDO reports `DefaultPawnClass` =
`/Game/ThirdPerson/Blueprints/BP_CombatCharacter.BP_CombatCharacter_C`; the class's component list
shows `SharedCameraComponent` as its own rather than inherited; the manager's EventGraph now reads
`ReceiveBeginPlay -> parent ReceiveBeginPlay` with no component call. Both C++ changes compiled
through Live Coding (Succeeded/Succeeded). Source: `WishCraftCharacter.cpp` `DoMove`/`DoLook`,
`Ability_Fireball.cpp` `BeginEffect`, `SharedCameraManager.cpp` `Tick`.

### P48 - A camera mode two machines can disagree about, and Enter as a second door into one submit path (Verified, then Source)

`ToggleSharedCamera` moved the *local* view target and nothing else. So each machine kept its own
answer: the host presses F3 and switches to follow, the client is still watching the framing camera,
and neither is wrong by its own lights. The camera mode is a fact about the match, not about a
machine, so it moved to the actor whose job is match-wide replicated truth -- `AWishCraftGameState`,
which already owns the ruleset.

```
F3 -> ToggleSharedCamera (client)
     -> ServerToggleSharedCamera                [Server, Reliable, on the character]
        -> GameState->ServerSetSharedCameraActive(!bSharedCameraActive)
           -> bSharedCameraActive replicated  [DOREPLIFETIME]
              -> OnRep_SharedCameraActive       (on every client)
                 -> ApplyCameraMode()
                    -> SetViewTargetWithBlend for LOCAL controllers only
```

Two details that are easy to get wrong. The server never receives its own RepNotify, so
`ServerSetSharedCameraActive` calls `OnRep_SharedCameraActive` directly -- the same pattern
`ServerSetRuleset` already used. And `ApplyCameraMode` skips non-local controllers, because a
listen server holding two controllers may only steer the one whose screen it is; the remote client
applies the same replicated flag on its own machine.

The setter is `public` for a mundane reason worth recording: the character calls it from its server
RPC, so a `protected` placement fails to compile at that call site, not at the declaration.

For the rule box, `WBP_RuleInput` had the submit chain welded to the Send button's OnClicked event --
cast the owning player, gate on `HasAuthority && IsLocalController`, `SubmitRuleText`, then the
status-text colour updates. Enter needed the same thing, and a second copy of that chain is two
places to fix the next time the status copy changes. The chain was moved into a function,
`SubmitRule`, and both events call it:

- `SendButton` -> OnClicked -> `SubmitRule`
- `RuleTextBox` -> OnTextCommitted -> `ETextCommit` switch -> only `OnEnter` -> `SubmitRule`

The switch matters: `OnTextCommitted` also fires when the box loses focus, so without it, clicking
away would submit whatever was half-typed.

**Evidence class (Verified, then Source).** Post-rebuild probe: `WishCraftGameState` CDO exposes
`shared_camera_active` with default `True`, `WishCraftCharacter` exposes `toggle_shared_camera` and
`using_shared_camera`; `WBP_RuleInput` function list contains `SubmitRule` (category `Rules`) and its
event list contains the `RuleTextBox` `OnEditableTextBoxCommittedEvent` binding. Build:
`rebuild_and_restart_editor` succeeded, editor reopened on `Lvl_Arena`. Source:
`WishCraftGameState.h/.cpp`, `WishCraftCharacter.h/.cpp`, `WBP_RuleInput` EventGraph + `SubmitRule`.
Note that a `hasattr` probe on a CDO reports false for engine-side UPROPERTYs and is not a valid
check -- enumerate `dir(cdo)` instead.

### P49 - Two machines, two camera modes, and the hook that was missing (Verified in source, then Source)

Symptom: at launch the server and the client sat in different camera modes, while every F3 press
synced correctly. The log proves the split -- each toggle printed exactly one
`Camera mode replicated: ...` pair per press, and nothing at launch, because no toggle had happened.

Cause: **the component set the view target in its own `BeginPlay`, and a pawn's BeginPlay is not when
a pawn has a controller.** On the server the order is `SpawnDefaultPawn` -> pawn `BeginPlay` ->
`Possess`, so `GetController()` was still null when `USharedCameraComponent::BeginPlay` ran, the
`if (PlayerControllerComp)` guard was false, and the whole block was skipped -- silently. That machine
kept the follow camera. On a client the controller usually arrives *with* the pawn, so the same block
ran and the client got the framing camera. Two machines, two answers, no error anywhere.

This was latent in P47. Before it, the manager added the components from a `Delay 1.0` sweep, which is
long after possession, so `BeginPlay` always found a controller. Moving the component onto the class
made `BeginPlay` run at pawn spawn -- earlier than possession -- and turned an ordering assumption
into a defect. The lesson is the general one: **a hook is defined by when the engine calls it, not by
how early it runs.**

Fix: one writer. The view-setting block is gone from the component (it keeps the manager lookup and
the authority-only `SetPlayer`), and `AWishCraftGameState::ApplyCameraMode` is now the only thing in
the project that sets a view target -- search for `SetViewTarget` across `Source/` and the plugin
returns exactly one call site. It is driven by two events:

1. `OnRep_SharedCameraActive` -- the replicated mode changed, or arrived for the first time;
2. `AWishCraftCharacter::NotifyControllerChanged` -- the possession hook. The engine calls it from
   `APawn::PossessedBy` (server, after the controller is set), from `APawn::OnRep_Controller` (client,
   after the replicated controller lands) and from `APawn::UnPossessed`. That is the first moment
   either machine can legitimately point its controller somewhere. It also covers respawn for free.

`ApplyCameraMode` now sets `bAutoManageActiveCameraTarget = false` itself, because otherwise the
controller re-targets its own pawn the instant it is possessed -- which would undo the mode once, at
launch, on whichever machine applied first.

**Evidence class (Verified in source, then Source).** Engine: `Source/Runtime/Engine/Private/Pawn.cpp`
-- `NotifyControllerChanged()` is invoked at the `OnRep_Controller` path (line ~643), from
`PossessedBy` (line ~709) and from `UnPossessed` (line ~735). Project: `grep SetViewTarget` over
`Source/` and `Plugins/SharedCameraSystem/Source/` returns one call site,
`WishCraftGameState.cpp:135`. Build: `rebuild_and_restart_editor` succeeded, editor reopened on
`Lvl_Arena`. **Not yet observed in a live launch** -- the launch path is the thing to look at in PIE,
and note that a single-player launch is now a valid smoke test because the server's own controller was
the one that failed.

### P50 - The camera belonged to the player, not the match (Verified, then Source)

P48 made the camera mode match-wide on the premise that "a camera mode is a fact about the match".
That premise was wrong, and it was inferred from the symptom rather than from the game: two machines
were applying *different defaults at different moments* (see P49), which reads exactly like a sync
failure and is not one. The user's requirement is the opposite of what P48 built -- each player sets
their own camera -- and the desync disappears for a structural reason once the state is per-machine:
there is no cross-machine value left to disagree about.

What came out: `bSharedCameraActive`, `ServerSetSharedCameraActive`, `OnRep_SharedCameraActive` and
`ApplyCameraMode` from `AWishCraftGameState`, and the `ServerToggleSharedCamera` RPC from
`AWishCraftCharacter`. Searching `Source/` for those four names returns nothing; the GameState is back
to owning `Ruleset` only. Executing that search *before* rebuilding is what caught the leftovers
cheaply -- a stale call site would have been a compile error anyway, but the reverse case (a symbol
still referenced from a Blueprint) would not have been.

Where the choice lives now: `AWishCraftPlayerController::bPrefersSharedCamera`, default `true`, and
deliberately **not** replicated. It sits on the controller rather than the pawn for one concrete
reason -- a respawn destroys the pawn, so a pawn-held preference would silently reset to shared every
time a player died. `bUsingSharedCamera` on the character stays as a per-machine mirror for Blueprint.

The toggle derives the current mode from `PlayerController->GetViewTarget() == SharedCamera` rather
than from the stored flag. The flag says what was asked for; the view target says what the machine
actually has, and those differ on any machine whose view was set from somewhere else.

Two ordering hazards, both handled in `AWishCraftCharacter`:

1. **When to apply.** `NotifyControllerChanged` -- the P49 hook, called from `PossessedBy` on the
   server and `OnRep_Controller` on a client. Not `BeginPlay`, which is what caused P49.
2. **What may not exist yet.** `ASharedCameraManager` is a separate level actor and replicates on its
   own schedule, so a client can be possessed before the manager arrives. Possession therefore starts
   a 0.1 s repeating timer; `ApplyPreferredCamera` clears it on the first apply that actually finds a
   manager, and also clears it immediately when the preference is *follow* (which needs no manager) or
the controller is not local. Unpossess fires the hook with no controller, so that path clears instead
of retrying forever.

`ApplyPreferredCamera` also sets `bAutoManageActiveCameraTarget = false`. Without it the controller
re-targets its newly possessed pawn after the apply and the choice is undone once, at launch, on
whichever machine got there first.

**Evidence class (Verified, then Source).** Build: `rebuild_and_restart_editor` succeeded, editor back
on `Lvl_Arena`. Project: `grep` for `bSharedCameraActive|ServerSetSharedCameraActive|ApplyCameraMode|ServerToggleSharedCamera|OnRep_SharedCameraActive`
across `Source/` returns nothing, and `SetViewTarget` now has a single call site in the whole project
(`WishCraftCharacter.cpp`, `ApplyPreferredCamera`). **Not observed in a live two-player launch** --
neither P49 nor P50 has been seen in PIE, so the launch behaviour remains read from source.

### P51 - The abilities were invisible because nothing pointed at the assets (Verified, then Source)

The cosmetic pass. The important discovery is that almost none of it needed new art: the project
already owned five Niagara systems and thirteen materials, and the three ability systems had **zero
referencers**. `AWishCraftCharacter::PlayTokenEffect` had been reading `StartEffect`/`SustainEffect`/
`EndEffect` off `FAbilityTokenDefinition` for several passes, and every one of those fields was empty,
so the function logged nothing and spawned nothing. The abilities were not unpolished; they were silent
no-ops.

**Token table (data only, no rebuild).** Fireball start -> `NS_Ability_FireballCast` on `hand_r`;
Invisibility start *and* end -> `NS_Ability_InvisibilityPulse` on `spine_03`, sustain deliberately empty
so a loop cannot outline a player who is meant to be unseen; SpeedBoost sustain -> `NS_Ability_SpeedTrail`
at the mesh origin (`AttachSocket = None`), because a trail belongs at the feet, not in a hand.

**Health bar.** `HealthWidgetComponent` on `BP_ThirdPersonCharacter` had
`TintColorAndOpacity = (0,0,0,0)`. That is the whole bug: the widget was built, attached and ticking,
and drawn at zero alpha. `DrawSize` at 500x500 looked like the culprit and is not -- `bDrawAtDesiredSize`
is `True`, so the component sizes to the widget's own root `SizeBox` (170x24) and `DrawSize` is unused.
Setting the tint to opaque white and lifting the component to `Z=132` was the entire fix.

**New VFX.** Five systems, all self-contained with no user parameters, because they are all spawned from
C++ via `UNiagaraFunctionLibrary`: `NS_Fireball_Trail` (world-space ribbon + glow, 150/s against a 22 uu
ribbon at 2500 uu/s), `NS_Fireball_Impact`, `NS_Hit_Flash`, `NS_Death_Dissolve`, `NS_Spawn_In`.
`M_VFX_Streak` had to gain its `NiagaraSprites`/`NiagaraRibbons` usage flags or a ribbon renderer would
have fallen back to the default material in a packaged build -- a defect that is invisible in PIE.

**Audio, and the honest caveat.** AI audio generation is not available on this plan, so every sound is a
procedural MetaSoundSource rather than a recorded or generated wave. The synthetic layers (UI chirp,
invisibility shimmer) are convincing; the fire and body impacts read as stylised synth hits with the
right punch and timing, not as recorded flesh and fire. Nine assets in `/Game/Sandbox/Audio`: seven
one-shots, two loops. Each one-shot carries `UE.Source.OneShot` with an explicit OnFinished trigger, so
`SpawnSoundAttached(..., bAutoDestroy=true)` cleans up instead of running forever -- a continuously
running generator would have leaked a component per cast. Ambient bed is a level `AmbientSound` actor;
the speed loop is held on a `UAudioComponent` in `TokenAudio`, keyed by token so it can be stopped.

**The struct change is the reason a rebuild was required.** `FAbilityTokenDefinition` gained
`StartSound`, `SustainSound` and `EndSound`. Live Coding patches function bodies only, so a delegation to
`python_agent` failed first with `Failed to find property 'StartSound' on 'AbilityTokenDefinition'` --
a *reflection* lookup failure, which is what proves the running binary was stale rather than the script
being wrong. Two earlier "interrupted" runs of the same script had been misread as tool flakiness. A
`rebuild_and_restart_editor` fixed it, and re-reading the property returned a clean `None`.

**Damage feedback.** `OnHealthChanged` is bound on the character, not `OnDeath`. The distinction matters:
`ApplyDamage` runs server-side only, but `Health` is a RepNotify and `OnRep_Health` broadcasts the same
delegate on every client, so one binding gives local feedback on every machine with no extra
replication. `LastKnownHealth` makes a hit a *decrease* -- a respawn refill and the first replicated
value both fire the delegate and must not spawn an impact.

Death was different: `HandleDeath` is server-only, so the dissolve needed a `NetMulticast` RPC of its own
(`MulticastDeathFeedback`) or the other player would have seen a character blink out. The dissolve is
spawned `KeepWorldPosition` so the ash rises where the player died rather than chasing a corpse the
respawn timer is about to destroy.

**Camera shake.** `CS_Impact` is a Blueprint subclass of `ULegacyCameraShake`; its oscillation fields are
`FOscillator` (`Amplitude`, `Frequency`, `Waveform`), **not** `FPerlinNoiseShaker` -- passing the latter
fails with `Cannot nativize 'PerlinNoiseShaker' as 'FOscillator'`. Configured via
`rot_oscillation`/`loc_oscillation`/`fov_oscillation`/`oscillator_time_remaining` (0.55/0.45/0.30 deg,
2.2 uu vertical, 1.5 FOV, 0.35 s). Only local controllers are shaken.

**Fireball body.** `M_Fireball_Body` is unlit emissive, two-colour by Fresnel (yellow-white core,
deep red silhouette, 6 -> 12 intensity) so the ball is its own light source; the actor also gained a
`UPointLightComponent` at 3200 cd / 520 uu, because a glowing sphere with no spill still reads as plastic.
The trail is a `UNiagaraComponent` **attached** to the projectile: that is correct here rather than a
world-spawned system, because the emitters are `local_space = false`, so the component supplies the spawn
origin and the particles stay where they were born.

**Level and UI.** `PPV_ArenaGrade` is an unbound PostProcessVolume (exposure pinned 1.6-2.4, bloom 0.95,
vignette 0.38, motion blur 0.12, +8% saturation, +6% contrast, a warm -3% blue gain) and `Ambient_Arena`
carries the bed at 0.6 volume. `WBP_RuleInput`'s `EditableTextBox` had **black text on a near-black panel**
-- the style's `TextStyle.ColorAndOpacity` was (0,0,0,1) -- so the host could not read what they typed.
Fixed with dark RoundedBox `BackgroundImageNormal/Hovered/Focused` brushes, light text, a blue focus
outline, an accent Send button and a one-line subtitle explaining what the panel does. The `EditableTextBoxStyle`
fields are `BackgroundImageNormal|Hovered|Focused|ReadOnly`; a plain `BackgroundImage` is rejected.

**Not done, deliberately.** Arena *geometry* dressing (the floor is still `MI_LDA_*` blockout) was left
alone: it is level-design work with real destructive risk and no way to judge the result without a human
looking at it. The match HUD is still blocked on scores and `bMatchOver` being server-only on
`AWishCraftGameMode` with no replication.

**Evidence class (Verified, then Source).** Build: a full `rebuild_and_restart_editor` was required and
succeeded, editor back on `Lvl_Arena`; the new struct field resolves through reflection. CDO check by
reflection: `HitEffect`, `DeathEffect`, `SpawnEffect`, `HitSound`, `DeathSound`, `SpawnSound` all resolve to
their named assets on `BP_ThirdPersonCharacter_C`. Token table read back shows both halves populated
(VFX and audio) for all three tokens. `HealthWidgetComponent.tint_color_and_opacity` reads
`(1,1,1,1)`. Widget appearance confirmed by offscreen render, not by eye in PIE. **None of the runtime VFX,
audio or feedback has been observed in a live two-player session** -- there is no PIE driver in this
session, so what is verified is that every reference resolves and every asset is bound, not that a
fireball looks good when it hits.

**Addendum: the one-shot systems did not stop looping.** Reported afterwards as a flash repeating on the
character with nothing triggering it. The log ruled out both triggers -- zero `Damage feedback` lines and
`Derived 0 ability component(s)`, so neither damage nor an ability was involved. The cause was at
**system scope**: every emitter's `Emitter State` was already `Once`, which is why reading only the
emitters found nothing, but each system's `System State` -> `Loop Behavior` was `Infinite`, and each
emitter's `Loop Duration Mode` was *also* `Infinite`, so the 1.0 loop duration was never enforced. The
system re-ran its bursts on a repeating cycle, never reached a completion state, and `bAutoDestroy` never
destroyed the component -- leaving it welded to the character. Fixed on `NS_Hit_Flash`, `NS_Spawn_In`,
`NS_Death_Dissolve` and `NS_Fireball_Impact`: `System State` -> `Once`, emitter `Loop Duration Mode` ->
`Fixed`. `NS_Fireball_Trail` deliberately stays `Infinite`, because C++ deactivates it on explosion.

The lesson: `bAutoDestroy = true` is not a promise that the system ends, only that it is cleaned up *if*
it does. And `snapshot_system` does not report system-scope loop behaviour at all -- the value has to be
read from the `System State` pin in `SystemUpdateScript`, which is exactly why the first look at the
emitters came back clean and the bug survived the original build.

**Addendum 2: velocity-oriented impact sparks, and a tooling hazard worth recording.** The fireball impact's
sparks were round dots because `ImpactStreaks` had real velocity data (`AddVelocity -> Drag ->
SolveForcesAndVelocity`) that the sprite renderer simply was not using for orientation. Fixed with
`sprite_alignment = VelocityAligned` plus a custom `NM_VelocityStretch` HLSL module after the solver, which
writes `Particles.SpriteSize.x = clamp(speed * LengthScale, 14, 175)` -- so a spark at ~1400 cm/s renders as
a ~155-unit streak and **shortens as Drag bleeds off the speed**. The speed limit was also raised 1000 ->
2400; at 1000 every fast spark was being clamped flat. `ImpactEmbers` deliberately stays `UNALIGNED` and
gravity-driven, which is what keeps the two layers reading as different materials.

The hazard: **`replace_emitter` and `build_system` report success while producing emitters with zero modules
and zero renderers, and they strip the asset.** That is how this system briefly ceased to exist mid-task; it
had to be re-authored through the commands that do work (`add_emitter`, `add_module`, `set_renderer`,
`set_module_input`). The reconstruction lost every per-particle dynamic-input chain, and the loss was only
visible by comparing module counts: `FlashCore` fell from 12 modules to 4 and `ImpactEmbers` lost its
`Color` attribute outright, so both popped at full brightness and vanished at end of life. Repaired with
`Color` + `ScaleColor` + a second custom module, `NM_ImpactColorRamp`.

Two things learned that apply beyond this asset. `set_module_input` accepts **literals only** -- it rejects
objects, so an over-life ramp cannot be authored through it, and no dynamic-input command exists at all;
a custom HLSL module is the only scriptable way to get a ramp. And `M_VFX_HotFlash` / `M_VFX_GlowDot` are
**Additive**, which ignores particle alpha, so an alpha-only fade does nothing visible -- `NM_ImpactColorRamp`
multiplies RGB by the fade term as well. Any future fade on an additive VFX material has to do the same.

The current impact is therefore functional and velocity-oriented but **not bit-identical to the art that
existed before the wipe**: randomised size ranges, the gradient lerp chains and the flash/ember size-growth
curves are gone, replaced by direct values. Restoring that sophistication needs the Niagara editor UI,
because the automation in this session cannot express it.

**Addendum 3: the impact system moved to `NS_Fireball_Impact1`, and Live Coding was not enough to move it.**
Requested as "use ns_fireball_impact1". Nothing pointed at the old system by asset reference --
`DA_AbilityTokens` does not list it and the Asset Registry reports zero referencers -- because it was
hardcoded in C++ at two call sites in `AbilityProjectile.cpp`: the `ConstructorHelpers::FObjectFinder` in
the constructor (`:69`, which is what actually assigns `ImpactEffect`) and a `LoadObject` runtime fallback
(`:204`) that only runs if the CDO field is null. Both now read
`/Game/Sandbox/VFX/NS_Fireball_Impact1.NS_Fireball_Impact1`.

The part worth recording: **Live Coding reported `Compile Succeeded / Patching Succeeded` and the change
was not live.** Reading the CDO back through reflection returned
`ImpactEffect -> /Game/Sandbox/VFX/NS_Fireball_Impact.NS_Fireball_Impact` -- the old asset. A constructor
body has already run by the time a patch is applied, and the class's default object is not rebuilt, so a
string literal in a constructor is exactly the kind of edit a hot patch cannot deliver. The check is one
reflection read of the CDO, and it cost a turn instead of a silently wrong PIE run:

```text
after TriggerLiveCoding  -> /Game/Sandbox/VFX/NS_Fireball_Impact.NS_Fireball_Impact    (stale)
after rebuild_and_restart -> /Game/Sandbox/VFX/NS_Fireball_Impact1.NS_Fireball_Impact1  (live)
```

`NS_Fireball_Impact1` is a copy of the repaired system, not a different effect: same three emitters, same
module GUIDs, same `NM_VelocityStretch` / `NM_ImpactColorRamp` wiring. It differs on `ImpactStreaks`, which
carries three modules the repaired original does not -- `AddVelocity` (fed by a `RandomVector`),
`UpdateMeshOrientation` -- for 12 modules against 9. Those write `Velocity` and `MeshOrientation`, and the
renderer on that emitter is a sprite, so `UpdateMeshOrientation` is inert there.

The old `NS_Fireball_Impact` was left on disk rather than deleted, because the impact system was re-authored
once already in this session and there is no source-control restore point for a `.uasset`.

**Evidence class (Verified, then Source).** Reflection read of the `AbilityProjectile` CDO before and after
the rebuild, and `does_asset_exist` true for both systems. Build: `rebuild_and_restart_editor` succeeded,
editor back on `Lvl_Arena`. Source: `AbilityProjectile.cpp:69,204`. **Not observed in PIE** -- what is
verified is that the class resolves the new asset, not how the impact reads when a fireball lands.

**Addendum 4: the copies were consolidated, and the hardcoded path did not follow.** The effect was then
edited by hand in the Niagara editor and the duplicate removed, leaving one system at the original path,
`/Game/Sandbox/VFX/NS_Fireball_Impact`. The C++ still named `NS_Fireball_Impact1`, which after the merge **no
longer loads at all** -- `EditorAssetLibrary.load_asset` returns `None` while `does_asset_exist` still
reports true, so a package is on disk but yields no object. That combination is the trap: an existence check
is not a resolvability check.

It looked fine at first for a reason worth writing down. The running editor's CDO held the UObject from the
earlier load, and that object had been *renamed*, so reflection reported
`ImpactEffect -> /Game/Sandbox/VFX/NS_Fireball_Impact` while the compiled-in string said `...Impact1`. The
pointer was correct and the path was dead. On the next launch `FObjectFinder` would have failed, the
`LoadObject` fallback would have failed against the same dead string, `ImpactEffect` would have been null,
and the fireball would have hit with no impact at all -- silently, because a `ConstructorHelpers` miss is a
log warning rather than a compile error.

Fixed by naming the real asset at both call sites and rebuilding. Verified from a **cold launch**, which is
the only state that tests this: `CDO ImpactEffect -> /Game/Sandbox/VFX/NS_Fireball_Impact`, class
`NiagaraSystem`. The stale `NS_Fireball_Impact1` package was left on disk, unreferenced.

The user's edit also changed the streaks' shape, so this is not the P51 Addendum 2 stack any more:
`ImpactStreaks` is 12 modules and `AddVelocityInCone` is gone, replaced by `AddVelocity` fed from a
`RandomVector`, with a `ScaleColor` whose alpha comes from a `FloatFromCurve`. `FlashCore` (7) and
`ImpactEmbers` (12) are untouched.

Two process notes. `rebuild_and_restart_editor` refuses while a PIE session is live, and
`LevelEditorSubsystem.is_in_play_in_editor()` reported **False** at the same moment the refusal fired -- the
guard is not reading that flag, so probe `UnrealEditorSubsystem.get_game_world()` instead. And a hardcoded
asset path is a *string*, so a rename is invisible to it: after any rename, grep `Source/` for the old name.

**Evidence class (Verified, then Source).** `load_asset` on both paths (one NiagaraSystem, one `None`),
`get_asset_meta` on `NS_Fireball_Impact` (3 emitters, module stacks above), CDO reflection read after a cold
launch. Build: `rebuild_and_restart_editor` succeeded, editor back on `Lvl_Arena`. Source:
`AbilityProjectile.cpp:69,204`. **Still not observed in PIE.**


## P52 — The design document was a plan; the code was the truth (Source)

The Nov 21 brief described a project that had never been built: an LLM returning JSON, a four-axis
composition model (element / role / delivery / shape, 144 combinations), Water Blast and Frost Shield,
and a `UElementDefinition` carrying element colours. None of that exists. Reading the brief against
`Source/` and `Content/` produced a second document's worth of differences, so the brief was rewritten
as **v2.0, as-built**, and the divergences were kept as numbered decisions rather than deleted.

What the source said, against what the plan said:

| Plan | Build |
| --- | --- |
| `EElement` / `ERole` / `EDelivery` / `EShape`, `FAbilityRecipe` | `EAbilityCategory` only, and `FAbilitySpec` = token name + param pairs |
| Replicated loadout on the PlayerState | Replicated `Ruleset` on `AWishCraftGameState`; the PlayerController's manager is a local mirror |
| Hosted LLM returning `power_scale`-style floats | Local FastAPI service on 8077 returning bucket labels only; the C++ validator maps labels through `ParamRanges` |
| Water Blast, Frost Shield | Fireball (Offense), Invisibility (Defense), SpeedBoost (Mobility) |
| `UStatusEffectComponent`, Burning / Slowed / Wet | does not exist |
| No camera section | `Plugins/SharedCameraSystem`, two modes, per-machine preference |

Two things the audit turned up that are worth carrying forward. `FAbilityParam` is a name/value pair
*on purpose* — a replicated `TMap` serialises to nothing, which is written into `AbilityTypes.h` as a
comment. And the two names the corpus can emit but the game cannot honour (`LightningStrike`, `Shield`)
are ten of the 45 residual rows, so the new Section 8.5 states them instead of leaving the number
unexplained.

**Evidence class (Source).** `AbilityTypes.h`, `AbilityTokenTable.h`, `AbilityLoadoutManager.h`,
`LoadoutValidator.cpp` (`GetCategoryCap` returns 1 for Defense, 99 otherwise), `WishCraftGameState.h`,
`WishCraftPlayerController.h`, `WishCraftCharacter.h`, `serve.py`, `catalogue_live.json`,
`IntentValidationTests.cpp` (five labels: `much_lower` / `lower` / `default` / `higher` / `max`), and
directory listings of `Content/Sandbox`, `Content/Input/Actions`, `Content/Maps`, `Plugins/`. No asset
was modified. The untested-parts claim in Section 9.2 is carried over from P51 Addendum 4 unchanged.








