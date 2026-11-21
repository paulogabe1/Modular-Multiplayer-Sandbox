#include "Abilities/AbilityLoadoutManager.h"

#include "Abilities/AbilityComponentBase.h"
#include "Abilities/AbilityTokenTable.h"
#include "Abilities/LoadoutValidator.h"
#include "Engine/World.h"
#include "GameFramework/GameStateBase.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "Net/UnrealNetwork.h"
#include "TimerManager.h"
#include "WishCraftCharacter.h"
#include "WishCraftGameState.h"
#include "WishCraftPlayerController.h"

UAbilityLoadoutManager::UAbilityLoadoutManager()
{
	PrimaryComponentTick.bCanEverTick = false;
	SetIsReplicatedByDefault(true);
}

void UAbilityLoadoutManager::BeginPlay()
{
	Super::BeginPlay();

	// Fall back to the project's token table so the system works without a Blueprint assignment.
	// The asset path is spelled in exactly one place now (UAbilityTokenTable::GetDefaultTable), because the
	// character also needs the table on machines that have no loadout manager to ask.
	if (!TokenTable)
	{
		TokenTable = UAbilityTokenTable::GetDefaultTable();
	}

	// The ruleset lives on the GameState, not here. Pull it before deriving anything, so the
	// components below always come from the match rules. PullRulesetFromGameState refreshes when it
	// finds rules; this explicit call covers a ruleset that is still empty.
	PullRulesetFromGameState();
	if (Loadout.Num() == 0)
	{
		RefreshAbilityComponents();
	}
}

void UAbilityLoadoutManager::EndPlay(const EEndPlayReason::Type EndPlayReason)
{
	ClearAbilityComponents();
	Super::EndPlay(EndPlayReason);
}

void UAbilityLoadoutManager::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);

	// Loadout is no longer replicated: it is a local mirror of AWishCraftGameState::Ruleset. Only the
	// per-player States array (cooldowns, active flags) still belongs to this component.
	DOREPLIFETIME(UAbilityLoadoutManager, States);
}

APawn* UAbilityLoadoutManager::GetOwningPawn() const
{
	if (const APlayerController* PC = Cast<APlayerController>(GetOwner()))
	{
		return PC->GetPawn();
	}
	return Cast<APawn>(GetOwner());
}

float UAbilityLoadoutManager::GetServerTimeSeconds() const
{
	if (const UWorld* World = GetWorld())
	{
		if (const AGameStateBase* GameState = World->GetGameState())
		{
			return GameState->GetServerWorldTimeSeconds();
		}
	}
	return 0.f;
}

TArray<FName> UAbilityLoadoutManager::GetEquippedTokens() const
{
	TArray<FName> Tokens;
	for (const FAbilitySpec& Spec : Loadout)
	{
		Tokens.Add(Spec.Token);
	}
	return Tokens;
}

FName UAbilityLoadoutManager::FindTokenByCategory(EAbilityCategory Category) const
{
	if (!TokenTable)
	{
		return NAME_None;
	}

	for (const FAbilitySpec& Spec : Loadout)
	{
		if (const FAbilityTokenDefinition* Definition = TokenTable->FindToken(Spec.Token))
		{
			if (Definition->Category == Category)
			{
				return Spec.Token;
			}
		}
	}
	return NAME_None;
}

float UAbilityLoadoutManager::GetCooldownRemaining(FName Token) const
{
	const FAbilitySpec* Spec = Loadout.FindByPredicate([Token](const FAbilitySpec& Candidate) { return Candidate.Token == Token; });
	if (!Spec)
	{
		return 0.f;
	}

	const float Cooldown = Spec->GetParam(FName(TEXT("Cooldown")), 0.f);
	if (Cooldown <= 0.f)
	{
		return 0.f;
	}

	const FAbilityState* State = States.FindByPredicate([Token](const FAbilityState& Candidate) { return Candidate.Token == Token; });
	if (!State)
	{
		return 0.f;
	}

	const float Elapsed = GetServerTimeSeconds() - State->LastActivationServerTime;
	return FMath::Max(0.f, Cooldown - Elapsed);
}

bool UAbilityLoadoutManager::IsTokenActive(FName Token) const
{
	const FAbilityState* State = States.FindByPredicate([Token](const FAbilityState& Candidate) { return Candidate.Token == Token; });
	return State ? State->bActive : false;
}

UAbilityComponentBase* UAbilityLoadoutManager::FindAbilityComponent(FName Token) const
{
	const TObjectPtr<UAbilityComponentBase>* Found = AbilityComponents.Find(Token);
	return Found ? Found->Get() : nullptr;
}

void UAbilityLoadoutManager::ClearAbilityComponents()
{
	for (const TPair<FName, TObjectPtr<UAbilityComponentBase>>& Pair : AbilityComponents)
	{
		if (UAbilityComponentBase* Component = Pair.Value.Get())
		{
			Component->DestroyComponent();
		}
	}
	AbilityComponents.Reset();

	if (UWorld* World = GetWorld())
	{
		for (TPair<FName, FTimerHandle>& Pair : EndTimers)
		{
			World->GetTimerManager().ClearTimer(Pair.Value);
		}
	}
	EndTimers.Reset();
}

void UAbilityLoadoutManager::RefreshAbilityComponents()
{
	APawn* Pawn = GetOwningPawn();
	if (!Pawn)
	{
		ClearAbilityComponents();
		return;
	}

	ClearAbilityComponents();

	if (!TokenTable)
	{
		return;
	}

	const bool bAuth = Pawn->HasAuthority();

	for (const FAbilitySpec& Spec : Loadout)
	{
		const FAbilityTokenDefinition* Definition = TokenTable->FindToken(Spec.Token);
		if (!Definition || !Definition->ComponentClass)
		{
			UE_LOG(LogSandbox, Warning, TEXT("Token %s has no component class in the table"), *Spec.Token.ToString());
			continue;
		}

		UAbilityComponentBase* Component = NewObject<UAbilityComponentBase>(Pawn, Definition->ComponentClass, NAME_None, RF_Transient);
		if (!Component)
		{
			continue;
		}

		Component->RegisterComponent();
		Component->ConfigureAbility(Spec.Token, *Definition, Spec.ToParamMap(), this, bAuth);
		AbilityComponents.Add(Spec.Token, Component);
	}

	UE_LOG(LogSandbox, Log, TEXT("Derived %d ability component(s) on %s (authority: %s)"),
		AbilityComponents.Num(), *GetNameSafe(Pawn), bAuth ? TEXT("yes") : TEXT("no"));
}

void UAbilityLoadoutManager::NotifyStateChanged()
{
	// Server-side equivalent of OnRep_State; keeps local state in sync without a round trip.
}

void UAbilityLoadoutManager::PullRulesetFromGameState()
{
	const UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	// A plain Cast rather than the GetGameState<T>() template: the template quietly returns null
	// when the level's GameStateClass is not this type, which is exactly the failure we want to see
	// in the log instead of silently swallowing.
	const AWishCraftGameState* GameState = Cast<AWishCraftGameState>(World->GetGameState());
	if (!GameState)
	{
		UE_LOG(LogSandbox, Warning, TEXT("Ruleset unavailable: this level's GameState is not AWishCraftGameState"));
		return;
	}

	if (GameState->Ruleset.Num() > 0)
	{
		ApplyRuleset(GameState->Ruleset);
	}
}

void UAbilityLoadoutManager::ApplyRuleset(const TArray<FAbilitySpec>& InSpecs)
{
	Loadout = InSpecs;

	// Only the server owns States. On a client they arrive by replication, so rebuilding them here
	// would be overwritten on the next update and would make the cooldown HUD flicker.
	const bool bAuthority = GetOwner() && GetOwner()->HasAuthority();
	if (bAuthority)
	{
		States.Reset();
		for (const FAbilitySpec& Spec : Loadout)
		{
			FAbilityState State;
			State.Token = Spec.Token;
			States.Add(State);
		}
	}

	UE_LOG(LogSandbox, Log, TEXT("Ruleset applied on %s (%d token(s), authority: %s)"),
		*GetNameSafe(GetOwner()), Loadout.Num(), bAuthority ? TEXT("yes") : TEXT("no"));

	RefreshAbilityComponents();
	NotifyStateChanged();
}

void UAbilityLoadoutManager::OnRep_State()
{
	NotifyStateChanged();
}

bool UAbilityLoadoutManager::ServerSetLoadout(const TArray<FName>& Tokens, FString& OutError)
{
	if (!GetOwner() || !GetOwner()->HasAuthority())
	{
		OutError = TEXT("Only the host can change the rules.");
		return false;
	}

	if (!TokenTable)
	{
		OutError = TEXT("No ability list is loaded, so the rules cannot be applied.");
		LastError = OutError;
		UE_LOG(LogSandbox, Error, TEXT("%s"), *OutError);
		return false;
	}

	const FLoadoutValidationResult Result = ULoadoutValidator::ValidateTokens(TokenTable, Tokens);
	return CommitValidatedRules(Result, OutError);
}

bool UAbilityLoadoutManager::ServerSetIntentJson(const FString& IntentJson, FString& OutError)
{
	if (!GetOwner() || !GetOwner()->HasAuthority())
	{
		OutError = TEXT("Only the host can change the rules.");
		return false;
	}
	const FLoadoutValidationResult Result = ULoadoutValidator::ValidateIntentJson(TokenTable, IntentJson);
	return CommitValidatedRules(Result, OutError);
}

bool UAbilityLoadoutManager::CommitValidatedRules(const FLoadoutValidationResult& Result, FString& OutError)
{
	// Both entry points check authority and validate before reaching this function.
	check(GetOwner() && GetOwner()->HasAuthority());
	OutError.Empty();
	if (!Result.bValid)
	{
		OutError = Result.Error;
		LastError = OutError;
		UE_LOG(LogSandbox, Warning, TEXT("Loadout rejected: %s"), *OutError);
		OnLoadoutMessage.Broadcast(OutError);
		return false;
	}

	LastError.Empty();

	// Publish to the replicated GameState instead of writing the result here. The GameState is the
	// one thing every machine can see, so this is what makes the rules match-wide, and it calls back
	// into ApplyRuleset on every manager, this one included.
	if (AWishCraftGameState* GameState = GetWorld() ? Cast<AWishCraftGameState>(GetWorld()->GetGameState()) : nullptr)
	{
		GameState->ServerSetRuleset(Result.Specs);
	}
	else
	{
		// No rules GameState in this world (a unit-test map, say). Fall back to this player only so
		// the host still plays with the rules it just typed.
		UE_LOG(LogSandbox, Warning, TEXT("No AWishCraftGameState found - applying the rules to this player only"));
		ApplyRuleset(Result.Specs);
	}

	const FString Message = FString::Printf(TEXT("Rules applied: %s"), *FString::JoinBy(Loadout, TEXT(", "), [](const FAbilitySpec& Spec) { return Spec.Token.ToString(); }));
	UE_LOG(LogSandbox, Log, TEXT("%s"), *Message);
	OnLoadoutMessage.Broadcast(Message);
	return true;
}

void UAbilityLoadoutManager::RequestSetLoadout(const TArray<FName>& Tokens)
{
	ServerSetLoadoutRPC(Tokens);
}

void UAbilityLoadoutManager::ServerSetLoadoutRPC_Implementation(const TArray<FName>& Tokens)
{
	FString Error;
	ServerSetLoadout(Tokens, Error);
}

void UAbilityLoadoutManager::RequestActivateCategory(EAbilityCategory Category)
{
	const FName Token = FindTokenByCategory(Category);
	if (Token.IsNone())
	{
		UE_LOG(LogSandbox, Log, TEXT("No ability equipped for that category"));
		return;
	}
	RequestActivateToken(Token);
}

void UAbilityLoadoutManager::RequestActivateToken(FName Token)
{
	if (Token.IsNone())
	{
		return;
	}

	// Server RPCs invoked on the authority run locally, so this is correct for host and clients.
	ServerActivateRPC(Token);
}

void UAbilityLoadoutManager::ServerActivateRPC_Implementation(FName Token)
{
	UAbilityComponentBase* Component = FindAbilityComponent(Token);
	if (!Component)
	{
		UE_LOG(LogSandbox, Log, TEXT("Activation ignored: %s is not equipped"), *Token.ToString());
		return;
	}

	if (!Component->CanActivate())
	{
		UE_LOG(LogSandbox, Log, TEXT("Activation ignored: %s is on cooldown (%.2fs)"), *Token.ToString(), GetCooldownRemaining(Token));
		return;
	}

	FAbilityState* State = States.FindByPredicate([Token](const FAbilityState& Candidate) { return Candidate.Token == Token; });
	if (!State)
	{
		FAbilityState NewState;
		NewState.Token = Token;
		States.Add(NewState);
		State = &States.Last();
	}

	State->LastActivationServerTime = GetServerTimeSeconds();
	State->bActive = true;

	const float Duration = Component->BeginEffect();
	PlayEffectOnPawn(Token, true);

	if (Duration > 0.f)
	{
		FTimerHandle& Handle = EndTimers.FindOrAdd(Token);
		GetWorld()->GetTimerManager().SetTimer(Handle,
			FTimerDelegate::CreateWeakLambda(this, [this, Token]() { EndActiveAbility(Token); }),
			Duration, false);
	}
	else
	{
		State->bActive = false;
	}

	NotifyStateChanged();
}

void UAbilityLoadoutManager::EndActiveAbility(FName Token)
{
	if (UAbilityComponentBase* Component = FindAbilityComponent(Token))
	{
		Component->EndEffect();
	}

	if (FAbilityState* State = States.FindByPredicate([Token](const FAbilityState& Candidate) { return Candidate.Token == Token; }))
	{
		State->bActive = false;
	}

	EndTimers.Remove(Token);
	PlayEffectOnPawn(Token, false);
	NotifyStateChanged();
}

void UAbilityLoadoutManager::PlayEffectOnPawn(FName Token, bool bStarting)
{
	// This manager lives on the PlayerController, and a PlayerController's RPCs only reach the
	// connection that owns it. The pawn replicates to everyone, so the cosmetic travels there.
	if (AWishCraftCharacter* WishCharacter = Cast<AWishCraftCharacter>(GetOwningPawn()))
	{
		WishCharacter->PlayAbilityEffect(Token, bStarting);
	}
}
