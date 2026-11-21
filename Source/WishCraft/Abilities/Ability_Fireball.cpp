#include "Abilities/Ability_Fireball.h"

#include "Abilities/AbilityProjectile.h"
#include "Engine/World.h"
#include "GameFramework/Pawn.h"
#include "GameFramework/PlayerController.h"
#include "SharedCameraManager.h"

namespace
{
	/** The framing camera, when it owns this pawn's view. */
	ASharedCameraManager* GetActiveSharedCamera(const APawn* Pawn)
	{
		const APlayerController* PlayerController = Cast<APlayerController>(Pawn->GetController());
		return PlayerController ? Cast<ASharedCameraManager>(PlayerController->GetViewTarget()) : nullptr;
	}

	/** The other player pawn in the match - what a shared-camera shot should lock onto. */
	APawn* FindOtherPlayerPawn(const APawn* Caster)
	{
		for (FConstPlayerControllerIterator It = Caster->GetWorld()->GetPlayerControllerIterator(); It; ++It)
		{
			const APlayerController* Other = It->Get();
			if (Other && Other->GetPawn() && Other->GetPawn() != Caster)
			{
				return Other->GetPawn();
			}
		}

		return nullptr;
	}
}

float UAbility_Fireball::BeginEffect()
{
	Super::BeginEffect();

	APawn* Pawn = GetAvatarPawn();
	if (!Pawn || !Pawn->HasAuthority())
	{
		return 0.f;
	}

	const float Speed = GetParam(TEXT("ProjectileSpeed"), 2500.f);
	const float InDamage = GetParam(TEXT("Damage"), 20.f);

	// Spawn from the character, not the camera. While the shared camera owns the view the view point
	// is the framing camera's, which put the fireball in mid-air between the two players.
	FVector SpawnOrigin = Pawn->GetActorLocation();
	SpawnOrigin.Z += Pawn->GetSimpleCollisionHalfHeight() * 0.6f;

	FRotator AimRotation = Pawn->GetActorRotation();
	if (AController* Controller = Pawn->GetController())
	{
		FVector ViewLocation;
		FRotator ViewRotation;
		Controller->GetPlayerViewPoint(ViewLocation, ViewRotation);
		AimRotation = ViewRotation;
	}

	// Under the framing camera the view is aimed at the midpoint of both players, so a view-relative
	// shot flies straight between them. Lock onto the opposing player instead.
	if (GetActiveSharedCamera(Pawn))
	{
		if (const APawn* Target = FindOtherPlayerPawn(Pawn))
		{
			const FVector AimPoint = Target->GetActorLocation() +
				FVector(0.f, 0.f, Target->GetSimpleCollisionHalfHeight() * 0.6f);
			AimRotation = (AimPoint - SpawnOrigin).Rotation();
		}
	}

	const FVector SpawnLocation = SpawnOrigin + AimRotation.Vector() * 80.f;

	FActorSpawnParameters SpawnParameters;
	SpawnParameters.Owner = Pawn;
	SpawnParameters.Instigator = Pawn;
	SpawnParameters.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButAlwaysSpawn;

	if (AAbilityProjectile* Projectile = GetWorld()->SpawnActor<AAbilityProjectile>(SpawnLocation, AimRotation, SpawnParameters))
	{
		Projectile->LaunchProjectile(Pawn->GetController(), Speed, InDamage);
		UE_LOG(LogSandbox, Log, TEXT("Fireball cast by %s"), *GetNameSafe(Pawn));
	}

	// Instant ability: no active duration.
	return 0.f;
}
