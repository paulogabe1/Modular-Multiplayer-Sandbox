// Copyright Epic Games, Inc. All Rights Reserved.

#include "WishCraftGameMode.h"

#include "Abilities/AbilityTypes.h"
#include "Abilities/HealthComponent.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "TimerManager.h"
#include "WishCraft.h"
#include "WishCraftGameState.h"

AWishCraftGameMode::AWishCraftGameMode()
{
	// Pawn and controller classes are set on the derived Blueprint (BP_ThirdPersonGameMode).
	// GameStateClass is deliberately NOT set here: that Blueprint overrides the property, and a
	// Blueprint value beats a constructor default. It is pinned in PreInitializeComponents instead.
}

void AWishCraftGameMode::PreInitializeComponents()
{
	// Set before Super. AGameModeBase::PreInitializeComponents is what reads GameStateClass to spawn
	// the GameState, and BP_ThirdPersonGameMode overrides the property to plain GameStateBase. This
	// assignment runs after the Blueprint has applied its values but before the spawn, so it is the
	// one place a Blueprint cannot override.
	GameStateClass = AWishCraftGameState::StaticClass();

	Super::PreInitializeComponents();
}

void AWishCraftGameMode::HandlePlayerDeath(APawn* DeadPawn, AActor* Killer)
{
	if (!HasAuthority() || !DeadPawn)
	{
		return;
	}

	APlayerController* VictimController = Cast<APlayerController>(DeadPawn->GetController());

	// The projectile passes its instigator, so the killer arrives as a pawn
	APawn* KillerPawn = Cast<APawn>(Killer);
	APlayerController* KillerController = KillerPawn ? Cast<APlayerController>(KillerPawn->GetController()) : nullptr;

	if (KillerController && KillerController != VictimController)
	{
		int32& KillerScore = Scores.FindOrAdd(TWeakObjectPtr<APlayerController>(KillerController));
		KillerScore++;

		UE_LOG(LogSandbox, Log, TEXT("%s killed %s (score %d/%d)"),
			*GetNameSafe(KillerController), *GetNameSafe(DeadPawn), KillerScore, ScoreLimit);

		if (KillerScore >= ScoreLimit)
		{
			bMatchOver = true;
			UE_LOG(LogSandbox, Log, TEXT("Match over - %s reached %d kills"), *GetNameSafe(KillerController), ScoreLimit);
		}
	}
	else
	{
		UE_LOG(LogSandbox, Log, TEXT("%s died with no credited killer"), *GetNameSafe(DeadPawn));
	}

	if (bMatchOver || !VictimController)
	{
		return;
	}

	// One timer per controller: the handle is local because the timer manager keeps the registration
	FTimerHandle RespawnHandle;
	GetWorldTimerManager().SetTimer(RespawnHandle,
		FTimerDelegate::CreateUObject(this, &AWishCraftGameMode::HandleRespawn, VictimController),
		FMath::Max(0.1f, RespawnDelay), false);
}

void AWishCraftGameMode::HandleRespawn(APlayerController* PlayerController)
{
	RespawnPlayer(PlayerController);
}

void AWishCraftGameMode::RespawnPlayer(APlayerController* PlayerController)
{
	if (!PlayerController || bMatchOver)
	{
		return;
	}

	// Replace the pawn outright so health, ability components and timers all start clean
	if (APawn* DeadPawn = PlayerController->GetPawn())
	{
		PlayerController->UnPossess();
		DeadPawn->Destroy();
	}

	RestartPlayer(PlayerController);

	APawn* NewPawn = PlayerController->GetPawn();
	if (!NewPawn)
	{
		UE_LOG(LogSandbox, Warning, TEXT("Respawn failed for %s - no PlayerStart found in the level"), *GetNameSafe(PlayerController));
		return;
	}

	if (UHealthComponent* NewHealth = NewPawn->FindComponentByClass<UHealthComponent>())
	{
		NewHealth->ResetHealth();
	}

	UE_LOG(LogSandbox, Log, TEXT("%s respawned as %s"), *GetNameSafe(PlayerController), *GetNameSafe(NewPawn));
}
