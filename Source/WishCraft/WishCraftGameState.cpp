// Copyright Epic Games, Inc. All Rights Reserved.

#include "WishCraftGameState.h"

#include "Abilities/AbilityLoadoutManager.h"
#include "Engine/World.h"
#include "Net/UnrealNetwork.h"
#include "WishCraft.h"
#include "WishCraftPlayerController.h"

AWishCraftGameState::AWishCraftGameState()
{
	// GameStateBase already replicates and is always relevant. Stated explicitly because the whole
	// design depends on it: a ruleset that does not reach every client would strand their loadouts.
	bReplicates = true;
	bAlwaysRelevant = true;
}

void AWishCraftGameState::GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const
{
	Super::GetLifetimeReplicatedProps(OutLifetimeProps);

	DOREPLIFETIME(AWishCraftGameState, Ruleset);
}

void AWishCraftGameState::ServerSetRuleset(const TArray<FAbilitySpec>& InSpecs)
{
	if (!HasAuthority())
	{
		return;
	}

	Ruleset = InSpecs;

	// The server never receives its own replication callback, so drive it manually here.
	OnRep_Ruleset();
}

void AWishCraftGameState::OnRep_Ruleset()
{
	UE_LOG(LogSandbox, Log, TEXT("Ruleset replicated: %s"),
		*FString::JoinBy(Ruleset, TEXT(", "), [](const FAbilitySpec& Spec) { return Spec.Token.ToString(); }));

	BroadcastRulesetChanged();
}

void AWishCraftGameState::BroadcastRulesetChanged()
{
	UWorld* World = GetWorld();
	if (!World)
	{
		return;
	}

	for (FConstPlayerControllerIterator It = World->GetPlayerControllerIterator(); It; ++It)
	{
		AWishCraftPlayerController* PlayerController = Cast<AWishCraftPlayerController>(It->Get());
		if (!PlayerController)
		{
			continue;
		}

		if (UAbilityLoadoutManager* Manager = PlayerController->GetLoadoutManager())
		{
			Manager->ApplyRuleset(Ruleset);
		}
	}
}
