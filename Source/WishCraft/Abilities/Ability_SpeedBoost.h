// Mobility slot: scales walking speed for a duration.
#pragma once

#include "CoreMinimal.h"
#include "Abilities/AbilityComponentBase.h"
#include "Ability_SpeedBoost.generated.h"

UCLASS()
class UAbility_SpeedBoost : public UAbilityComponentBase
{
	GENERATED_BODY()

public:
	virtual float BeginEffect() override;
	virtual void HandleCosmetic(bool bStarting) override;

protected:
	void ApplySpeed(bool bBoosted);

	UPROPERTY()
	float CachedWalkSpeed = 0.f;

	UPROPERTY()
	bool bSpeedApplied = false;
};
