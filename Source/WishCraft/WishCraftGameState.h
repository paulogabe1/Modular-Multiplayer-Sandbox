// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameStateBase.h"
#include "Abilities/AbilityTypes.h"
#include "WishCraftGameState.generated.h"

/**
 *  Replicated home of the match-wide ruleset.
 *
 *  The GameMode exists on the server only, so it cannot own state the clients need. The GameState
 *  replicates to every client, which makes it the correct single owner of "the rules of this match".
 *  Each player's loadout manager keeps only a local mirror, refreshed whenever this array changes.
 */
UCLASS()
class AWishCraftGameState : public AGameStateBase
{
	GENERATED_BODY()

public:

	AWishCraftGameState();

	/** Which tokens are equipped this match, with their already-clamped parameters. */
	UPROPERTY(ReplicatedUsing = OnRep_Ruleset, BlueprintReadOnly, Category = "Rules")
	TArray<FAbilitySpec> Ruleset;

	/** Server only: replaces the ruleset and re-derives every player's abilities from it. */
	void ServerSetRuleset(const TArray<FAbilitySpec>& InSpecs);

	/** Re-derives every player's ability components from the current ruleset. */
	void BroadcastRulesetChanged();

	virtual void GetLifetimeReplicatedProps(TArray<FLifetimeProperty>& OutLifetimeProps) const override;

protected:

	UFUNCTION()
	void OnRep_Ruleset();
};
