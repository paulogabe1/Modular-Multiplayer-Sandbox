#include "Abilities/Ability_SpeedBoost.h"

#include "GameFramework/Character.h"
#include "GameFramework/CharacterMovementComponent.h"
#include "GameFramework/Pawn.h"

float UAbility_SpeedBoost::BeginEffect()
{
	Super::BeginEffect();
	return GetParam(TEXT("Duration"), 5.f);
}

void UAbility_SpeedBoost::HandleCosmetic(bool bStarting)
{
	Super::HandleCosmetic(bStarting);

	// MaxWalkSpeed is not replicated in UE5, so every machine applies the same change locally
	// and the server keeps authority over the resulting movement.
	ApplySpeed(bStarting);
}

void UAbility_SpeedBoost::ApplySpeed(bool bBoosted)
{
	ACharacter* Character = Cast<ACharacter>(GetAvatarPawn());
	if (!Character)
	{
		return;
	}

	UCharacterMovementComponent* Movement = Character->GetCharacterMovement();
	if (!Movement)
	{
		return;
	}

	if (bBoosted)
	{
		if (bSpeedApplied)
		{
			return;
		}
		CachedWalkSpeed = Movement->MaxWalkSpeed;
		Movement->MaxWalkSpeed = CachedWalkSpeed * GetParam(TEXT("Multiplier"), 1.5f);
		bSpeedApplied = true;
	}
	else if (bSpeedApplied)
	{
		Movement->MaxWalkSpeed = CachedWalkSpeed;
		bSpeedApplied = false;
	}

	UE_LOG(LogSandbox, Log, TEXT("Speed boost %s for %s (MaxWalkSpeed %.0f)"),
		bBoosted ? TEXT("on") : TEXT("off"), *GetNameSafe(Character), Movement->MaxWalkSpeed);
}
