// Copyright Epic Games, Inc. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"
#include "WishCraftGameMode.generated.h"

/**
 *  Simple GameMode for a third person game
 *  Also drives the first-to-N kill loop used by the rules sandbox
 */
UCLASS(abstract)
class AWishCraftGameMode : public AGameModeBase
{
	GENERATED_BODY()

public:
	
	/** Constructor */
	AWishCraftGameMode();

	/** Kills needed to win the match */
	UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category="Match")
	int32 ScoreLimit = 3;

	/** Seconds between a death and the respawn */
	UPROPERTY(EditDefaultsOnly, BlueprintReadOnly, Category="Match")
	float RespawnDelay = 2.f;

	/** Set once someone reaches ScoreLimit; respawns stop */
	UPROPERTY(BlueprintReadOnly, Category="Match")
	bool bMatchOver = false;

	/** Server only: called by the character when its health reaches zero */
	void HandlePlayerDeath(APawn* DeadPawn, AActor* Killer);

	/** Server only: swaps the dead pawn for a fresh one at a PlayerStart */
	void RespawnPlayer(APlayerController* PlayerController);

protected:

	/**
	 * Pins GameStateClass before the engine spawns the GameState.
	 *
	 * BP_ThirdPersonGameMode sets GameStateClass explicitly, and a Blueprint property override beats
	 * a C++ constructor default. This override runs after the Blueprint has applied its values but
	 * before AGameModeBase reads the property to spawn the GameState, so it is the one place a
	 * Blueprint cannot override.
	 */
	virtual void PreInitializeComponents() override;

	/** Kill tally per controller for the current match */
	TMap<TWeakObjectPtr<APlayerController>, int32> Scores;

	/** Timer callback wrapper so each pending respawn keeps its own handle */
	void HandleRespawn(APlayerController* PlayerController);
};
